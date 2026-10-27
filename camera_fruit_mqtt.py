import glob
import json
import os
import shutil
import ssl
import subprocess
import time
from datetime import datetime

import cv2
import requests
import paho.mqtt.client as mqtt
from google.cloud import firestore
from dotenv import load_dotenv

load_dotenv()   # same .env as edge_receiver.py (CLOUD_BROKER / CLOUD_USER / CLOUD_PASS)

# ================= CONFIG ================= #
# IMPORTANT: this must be the Space's *app* address (…hf.space/analyze),
# NOT the Hugging Face web page (huggingface.co/spaces/...).
API_URL = os.getenv("API_URL", "https://dasunz-tomato-ai-analysis-v5.hf.space/analyze")
API_RETRIES = 3          # tries per scan
API_TIMEOUT = 120        # seconds (a sleeping Space needs 1-2 min to wake up)
SCAN_TYPE = "fruit"
SAVE_PATH = "captured_fruit.jpg"
FIREBASE_KEY_FILE = "serviceAccountKey.json"
LOG_FILE = "FruitScanLog.txt"

# Camera: "auto" = try every /dev/video* until one really gives a picture.
# Or set a number (e.g. 1) / a device path (e.g. "/dev/video1") in .env: CAMERA_INDEX=1
CAMERA_INDEX = os.getenv("CAMERA_INDEX", "auto")
WARMUP_FRAMES = 8          # first frames are often dark / empty

# Where the picture comes from:
#   "camera"          -> take ONE photo from the camera (default)
#   a file path       -> send that image file to the model (e.g. test.jpg)
#   a folder path     -> send the NEWEST image in that folder (e.g. images/)
# Set in .env :  IMAGE_SOURCE=camera   or   IMAGE_SOURCE=images
IMAGE_SOURCE = os.getenv("IMAGE_SOURCE", "camera")

# Time between captures (seconds)
# Testing: 30  |  Production: 3600 (60 minutes)
CAPTURE_INTERVAL = 30

# ================= MQTT CONFIG ================= #
BROKER = os.getenv("CLOUD_BROKER")
PORT = int(os.getenv("CLOUD_PORT", "8883"))
USERNAME = os.getenv("CLOUD_USER")
PASSWORD = os.getenv("CLOUD_PASS")
FRUIT_TOPIC = "greenhouse/fruit_result"   # mobile app subscribes here

for _name, _val in (("CLOUD_BROKER", BROKER), ("CLOUD_USER", USERNAME), ("CLOUD_PASS", PASSWORD)):
    if not _val:
        raise RuntimeError(f"{_name} is missing from .env")

# ================= FIRESTORE SETUP ================= #
db = firestore.Client.from_service_account_json(FIREBASE_KEY_FILE)
print("[FIRESTORE] Connected to Firestore project.")

# ================= MQTT SETUP ================= #
def make_client(client_id):
    """Works with Paho 1.x and 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)

client = make_client("fruit_camera")
client.username_pw_set(USERNAME, PASSWORD)
if os.getenv("CLOUD_TLS_INSECURE", "0") == "1":
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
else:
    client.tls_set()
client.reconnect_delay_set(min_delay=1, max_delay=30)

def on_connect(c, userdata, flags, rc):
    print(f"[MQTT] Connected. rc={rc}")

client.on_connect = on_connect
print("[INFO] Connecting to MQTT broker...")
client.connect_async(BROKER, PORT, 60)     # does not crash if the network is down
client.loop_start()

# ================= CAMERA ================= #
_camera_source = None      # remembered working camera (index or path)

def _open(source):
    """Open a camera with the V4L2 backend (no FFmpeg guessing)."""
    cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap

def _grab_frame(cap):
    frame = None
    for _ in range(WARMUP_FRAMES):
        ok, f = cap.read()
        if ok and f is not None:
            frame = f
        time.sleep(0.05)
    return frame

def _device_number(text):
    """'/dev/video2' -> 2 ,  '2' -> 2  (this OpenCV build opens by INDEX only, not by path)."""
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    return int(digits) if digits else None

def _candidate_sources():
    """Return a list of integer camera indexes to try."""
    if CAMERA_INDEX != "auto":
        n = _device_number(CAMERA_INDEX)
        return [n] if n is not None else []
    nums = sorted(
        n for n in (_device_number(p) for p in glob.glob("/dev/video*")) if n is not None
    )
    return nums

def capture_with_opencv():
    global _camera_source

    devices = _candidate_sources()
    if not devices:
        print("[ERROR] No /dev/video* devices exist. The OS does not see any camera.")
        print("        Check: ls /dev/video*   |   lsusb   |   dmesg | tail -30")
        return False

    # Try the camera that worked last time first
    if _camera_source is not None and _camera_source in devices:
        devices = [_camera_source] + [d for d in devices if d != _camera_source]

    for idx in devices:
        cap = _open(idx)
        if cap is None:
            print(f"[CAM] /dev/video{idx}: cannot open (not a capture device / busy / no permission)")
            continue
        frame = _grab_frame(cap)
        cap.release()
        if frame is None:
            print(f"[CAM] /dev/video{idx}: opened but no picture")
            continue

        cv2.imwrite(SAVE_PATH, frame)
        _camera_source = idx
        print(f"[OK] Image captured from /dev/video{idx}: {SAVE_PATH}")
        return True

    _camera_source = None
    print(f"[ERROR] Tried indexes {devices} - none gave a picture.")
    print("        Run:  v4l2-ctl --list-devices   and   lsusb   to see which one is the camera.")
    return False

def capture_with_fswebcam():
    """Fallback: fswebcam takes ONE still photo (sudo apt install fswebcam)."""
    if shutil.which("fswebcam") is None:
        print("[CAM] fswebcam not installed (sudo apt install fswebcam) - skipping fallback")
        return False
    for idx in _candidate_sources():
        dev = f"/dev/video{idx}"
        try:
            r = subprocess.run(
                ["fswebcam", "-d", dev, "-r", "1280x720", "--no-banner",
                 "-S", "10", "--jpeg", "90", SAVE_PATH],
                capture_output=True, timeout=20
            )
        except Exception as e:
            print(f"[CAM] fswebcam {dev} failed: {e}")
            continue
        if r.returncode == 0 and os.path.exists(SAVE_PATH):
            print(f"[OK] Image captured with fswebcam from {dev}: {SAVE_PATH}")
            return True
    return False


def use_image_file():
    """IMAGE_SOURCE = file or folder: send an existing image to the model."""
    src = IMAGE_SOURCE
    if os.path.isdir(src):
        files = [f for ext in ("*.jpg", "*.jpeg", "*.png", "*.JPG", "*.JPEG", "*.PNG")
                 for f in glob.glob(os.path.join(src, ext))]
        if not files:
            print(f"[ERROR] No images found in folder '{src}'")
            return False
        src = max(files, key=os.path.getmtime)
    if not os.path.isfile(src):
        print(f"[ERROR] Image file not found: {src}")
        return False
    if os.path.abspath(src) != os.path.abspath(SAVE_PATH):
        shutil.copyfile(src, SAVE_PATH)
    print(f"[OK] Using image file: {src}")
    return True


def capture_image():
    """Get ONE image into SAVE_PATH (camera photo or an existing file)."""
    if IMAGE_SOURCE != "camera":
        return use_image_file()
    if capture_with_opencv():
        return True
    return capture_with_fswebcam()


# ================= SEND TO API ================= #
def send_to_api():
    if "huggingface.co/spaces" in API_URL:
        print("[ERROR] API_URL is the Hugging Face WEB PAGE, not the API.")
        print("        Use: https://dasunz-tomato-ai-analysis-v5.hf.space/analyze")
        return None

    for attempt in range(1, API_RETRIES + 1):
        try:
            with open(SAVE_PATH, "rb") as img_file:
                files = {"file": img_file}
                data = {"scan_type": SCAN_TYPE}
                print(f"[INFO] Sending image to API (try {attempt}/{API_RETRIES})...")
                response = requests.post(API_URL, files=files, data=data, timeout=API_TIMEOUT)

            if response.status_code == 200:
                result = response.json()
                print(f"[OK] API Response: {result}")
                return result

            print(f"[ERROR] API Error - Status Code: {response.status_code}")
            print(f"Response: {response.text[:300]}")
            # 502/503/504 = Space is sleeping / starting -> wait and retry
            if response.status_code in (502, 503, 504) and attempt < API_RETRIES:
                print("[INFO] Space may be waking up, retrying in 15 s...")
                time.sleep(15)
                continue
            return None

        except requests.exceptions.Timeout:
            print("[ERROR] Request timed out.")
        except requests.exceptions.ConnectionError as e:
            print(f"[ERROR] Cannot reach the API: {e}")
            print("        'Name or service not known' = this device has no internet / DNS.")
            print("        Test:  ping -c 3 8.8.8.8   and   nslookup dasunz-tomato-ai-analysis-v5.hf.space")
        except Exception as e:
            print(f"[ERROR] {e}")
            return None

        if attempt < API_RETRIES:
            time.sleep(10)

    return None

# ================= PUBLISH TO MQTT + WRITE TO FIRESTORE ================= #
def publish_result(result):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    payload = {
        "analysis_type": result.get("analysis_type", "fruit"),
        "total_fruits": result.get("total_fruits", 0),
        "harvest_status": result.get("harvest_status", "Unknown"),
        "timestamp": timestamp
    }

    # --- MQTT publish (mobile app real-time) ---
    try:
        info = client.publish(FRUIT_TOPIC, json.dumps(payload), qos=1)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            print(f"[OK] Published to MQTT topic '{FRUIT_TOPIC}': {payload}")
        else:
            print(f"[MQTT] Publish failed: rc={info.rc}")
    except Exception as e:
        print(f"[MQTT] Publish failed: {e}")

    # --- Firestore write (persistent storage) ---
    try:
        db.collection("fruit_scans").add(payload)
        print("[FIRESTORE] Written to 'fruit_scans' collection successfully.")
        print(f"=== DATA SENT SUCCESSFULLY | {timestamp} ===")
        print(f"    Total Fruits  : {payload['total_fruits']}")
        print(f"    Harvest Status: {payload['harvest_status']}\n")
    except Exception as e:
        print(f"[FIRESTORE ERROR] Could not write to Firestore: {e}\n")

    # --- Local log write ---
    try:
        with open(LOG_FILE, "a") as f:
            f.write(
                f"{timestamp} | "
                f"Total Fruits={payload['total_fruits']} | "
                f"Harvest Status={payload['harvest_status']}\n"
            )
        print(f"[LOG] Written to {LOG_FILE}")
    except OSError as e:
        print(f"[LOG] Could not write log: {e}")

# ================= MAIN LOOP ================= #
if __name__ == "__main__":
    print("=== Fruit Scan: Camera -> API -> MQTT + Firestore -> Mobile App ===\n")
    while True:
        print(f"--- New Scan: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
        if capture_image():
            result = send_to_api()
            if result:
                publish_result(result)
            else:
                print("[WARN] No result to publish (API call failed).\n")
        else:
            print("[WARN] Skipping this cycle (camera capture failed).\n")
        print(f"[INFO] Waiting {CAPTURE_INTERVAL} seconds until next scan...\n")
        time.sleep(CAPTURE_INTERVAL)
