import cv2
import requests
import json
import ssl
import time
from datetime import datetime
import paho.mqtt.client as mqtt
from google.cloud import firestore

# ================= CONFIG ================= #
API_URL = "https://dasunz-tomato-ai-analysis-v5.hf.space/analyze"
SCAN_TYPE = "fruit"
SAVE_PATH = "captured_fruit.jpg"
CAMERA_INDEX = 0
FIREBASE_KEY_FILE = "serviceAccountKey.json"
LOG_FILE = "FruitScanLog.txt"

# Time between captures (seconds)
# Testing: 30  |  Production: 3600 (60 minutes)
CAPTURE_INTERVAL = 30

# ================= FIRESTORE SETUP ================= #
db = firestore.Client.from_service_account_json(FIREBASE_KEY_FILE)
print("[FIRESTORE] Connected to Firestore project.")

# ================= MQTT CONFIG (same broker as edge_receiver.py) ================= #
BROKER = "c0eacb6e24dd4984814b3e19f4daa7a4.s1.eu.hivemq.cloud"
PORT = 8883
USERNAME = "greenhouse_admin"
PASSWORD = "Admin@123"
FRUIT_TOPIC = "greenhouse/fruit_result"   # mobile app subscribes here

# ================= MQTT SETUP ================= #
client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)
print("[INFO] Connecting to MQTT broker...")
client.connect(BROKER, PORT, 60)
client.loop_start()
print("[OK] Connected to MQTT broker.\n")

# ================= CAPTURE IMAGE ================= #
def capture_image():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("[ERROR] Could not open camera.")
        return False
    ret, frame = cap.read()
    cap.release()
    if not ret:
        print("[ERROR] Failed to capture image.")
        return False
    cv2.imwrite(SAVE_PATH, frame)
    print(f"[OK] Image captured: {SAVE_PATH}")
    return True

# ================= SEND TO API ================= #
def send_to_api():
    try:
        with open(SAVE_PATH, "rb") as img_file:
            files = {"file": img_file}
            data = {"scan_type": SCAN_TYPE}
            print("[INFO] Sending image to API...")
            response = requests.post(API_URL, files=files, data=data, timeout=60)
        if response.status_code == 200:
            result = response.json()
            print(f"[OK] API Response: {result}")
            return result
        else:
            print(f"[ERROR] API Error - Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out.")
        return None
    except Exception as e:
        print(f"[ERROR] {e}")
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
    client.publish(FRUIT_TOPIC, json.dumps(payload))
    print(f"[OK] Published to MQTT topic '{FRUIT_TOPIC}':")
    print(f"     {payload}")

    # --- Firestore write (persistent storage) ---
    try:
        db.collection("fruit_scans").add(payload)
        print(f"[FIRESTORE] Written to 'fruit_scans' collection successfully.")
        print(f"=== DATA SENT SUCCESSFULLY | {timestamp} ===")
        print(f"    Total Fruits  : {payload['total_fruits']}")
        print(f"    Harvest Status: {payload['harvest_status']}\n")
    except Exception as e:
        print(f"[FIRESTORE ERROR] Could not write to Firestore: {e}\n")

    # --- Local log write ---
    with open(LOG_FILE, "a") as f:
        f.write(
            f"{timestamp} | "
            f"Total Fruits={payload['total_fruits']} | "
            f"Harvest Status={payload['harvest_status']}\n"
        )
    print(f"[LOG] Written to {LOG_FILE}")

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
