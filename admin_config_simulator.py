"""
Simulates the Mobile App's Admin Configuration screen.
Run this to update Firestore settings without needing the actual mobile app.
"""
from google.cloud import firestore

FIREBASE_KEY_FILE = "serviceAccountKey.json"

db = firestore.Client.from_service_account_json(FIREBASE_KEY_FILE)
doc_ref = db.collection("configuration").document("settings")

print("=== Admin Config Simulator ===")
print("Press Enter to keep the current value for any field.\n")

target_temp = input("Target Temperature (°C): ").strip()
target_humid = input("Target Humidity (%): ").strip()
target_moist = input("Target Soil Moisture (%): ").strip()
target_light = input("Target Light (Lux): ").strip()

update_data = {}
if target_temp:
    update_data["targetTemp"] = float(target_temp)
if target_humid:
    update_data["targetHumid"] = float(target_humid)
if target_moist:
    update_data["targetMoist"] = float(target_moist)
if target_light:
    update_data["targetLight"] = float(target_light)

if update_data:
    print("\n[INFO] Sending update to Firestore... (waiting up to 15 seconds)")
    try:
        doc_ref.set(update_data, merge=True, timeout=15)
        print(f"[OK] Firestore updated: {update_data}")
        print("Check the edge_receiver.py terminal - it should update automatically.")
    except Exception as e:
        print(f"[ERROR] Firestore write failed: {e}")
        print("This usually means a network/firewall issue is blocking the connection.")
else:
    print("\n[INFO] No changes made.")
