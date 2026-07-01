import cv2
import requests
from datetime import datetime

# ================= CONFIG ================= #
API_URL = "https://dasunz-tomato-ai-analysis-v5.hf.space/analyze"
SCAN_TYPE = "fruit"          # fixed - for fruit only
SAVE_PATH = "captured_fruit.jpg"
CAMERA_INDEX = 0             # USB camera index (0 = default)

# ================= CAPTURE IMAGE ================= #
def capture_image():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("[ERROR] Could not open camera. Check connection.")
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

            print("[INFO] Sending image to API... (cold start may take 30-40s)")
            response = requests.post(API_URL, files=files, data=data, timeout=60)

        if response.status_code == 200:
            result = response.json()
            print("\n========== DATA SENT SUCCESSFULLY ==========")
            print(f"Time     : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Response : {result}")
            print("==============================================\n")
            return result
        else:
            print(f"[ERROR] API Error - Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            return None

    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out - server did not respond (cold start delay)")
        return None
    except Exception as e:
        print(f"[ERROR] {e}")
        return None

# ================= MAIN ================= #
if __name__ == "__main__":
    print("=== Camera to API Test Script ===\n")

    if capture_image():
        send_to_api()
