import csv
import json
import ssl
import os
from datetime import datetime
import paho.mqtt.client as mqtt

CSV_FILE = "green_house/sensor_data.csv"

# --- MQTT CONFIGURATION (same broker, ESP32's publish topic) --- #
BROKER = "c0eacb6e24dd4984814b3e19f4daa7a4.s1.eu.hivemq.cloud"
PORT = 8883
USERNAME = "greenhouse_admin"
PASSWORD = "Admin@123"
SENSOR_RAW_TOPIC = "greenhouse/sensors/environment"   # ESP32 publishes here

CSV_HEADER = ["timestamp", "device", "temp", "humidity", "light", "soil_moisture", "soil_temp"]

# ================= ENSURE CSV EXISTS WITH HEADER ================= #
def ensure_csv():
    os.makedirs(os.path.dirname(CSV_FILE), exist_ok=True)
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
        print(f"[INFO] Created new CSV file with header: {CSV_FILE}")

# ================= MQTT CALLBACKS ================= #
def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected with result code {rc}")
    client.subscribe(SENSOR_RAW_TOPIC)
    print(f"[MQTT] Subscribed to '{SENSOR_RAW_TOPIC}'")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[ERROR] Could not parse message: {e}")
        return

    device = data.get("device_id", "UNKNOWN")

    # If a sensor fails on the ESP32 side (e.g. DHT read error), the field
    # arrives as None. We don't discard the whole row for that - we just
    # default the broken field to 0 so the rest of the real data still
    # reaches the CSV / mobile app.
    temp = data.get("temperature_2")        # air temperature
    soil_temp = data.get("temperature_1")   # soil temperature
    humidity = data.get("humidity")
    light = data.get("light_lux")
    soil_moisture = data.get("soil_moisture")

    raw_values = {
        "temp": temp, "soil_temp": soil_temp,
        "humidity": humidity, "light": light, "soil_moisture": soil_moisture
    }
    failed_fields = [k for k, v in raw_values.items() if v is None]
    if failed_fields:
        print(f"[WARN] Sensor read failed for: {failed_fields} - defaulting to 0")

    temp = temp if temp is not None else 0
    soil_temp = soil_temp if soil_temp is not None else 0
    humidity = humidity if humidity is not None else 0
    light = light if light is not None else 0
    soil_moisture = soil_moisture if soil_moisture is not None else 0

    timestamp = datetime.now().strftime("%Y-%m-%d | %H:%M")

    row = [timestamp, device, temp, humidity, light, soil_moisture, soil_temp]

    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(row)

    print(f"[OK] Row written to CSV: {row}")

# ================= MAIN ================= #
if __name__ == "__main__":
    ensure_csv()

    client = mqtt.Client()
    client.username_pw_set(USERNAME, PASSWORD)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    print("Connecting to Cloud Broker (CSV writer)...")
    client.connect(BROKER, PORT, 60)
    client.loop_forever()
