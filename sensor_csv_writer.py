import csv
import json
import math
import os
import time
from datetime import datetime

import paho.mqtt.client as mqtt

# ============================================================
# CONFIGURATION
# ============================================================

CSV_FILE = "green_house/sensor_data.csv"
MAX_ROWS = 1000

# Local Mosquitto broker running on the Edge device (Orange Pi)
LOCAL_BROKER = "127.0.0.1"
LOCAL_PORT = 1883

# Topic published by the hybrid ESP32 code (Primary path)
SENSOR_TOPIC = "greenhouse/local/telemetry"

CLIENT_ID = "greenhouse_csv_logger"

CSV_HEADER = [
    "timestamp", "device", "temp", "humidity",
    "light", "soil_moisture", "soil_temp"
]

# ============================================================
# CREATE CSV FILE IF IT DOES NOT EXIST
# ============================================================

def ensure_csv():
    folder = os.path.dirname(CSV_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)
        print(f"[INFO] CSV file ready: {CSV_FILE}")

# ============================================================
# TRIM CSV TO LATEST 1000 ROWS
# ============================================================

def trim_csv():
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            ensure_csv()
            return
        header = rows[0]
        data_rows = rows[1:]
        if len(data_rows) > MAX_ROWS:
            data_rows = data_rows[-MAX_ROWS:]
            temp_file = CSV_FILE + ".tmp"
            with open(temp_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)
                writer.writerows(data_rows)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, CSV_FILE)
            print(f"[INFO] CSV trimmed to latest {MAX_ROWS} rows.")
    except (OSError, csv.Error) as e:
        print(f"[WARN] CSV trim failed: {e}")

# ============================================================
# VALIDATE SENSOR VALUES
# Missing / null / non-finite values default to 0
# ============================================================

def get_sensor_value(data, field_name):
    value = data.get(field_name)
    if value is None or isinstance(value, bool):
        print(f"[WARN] '{field_name}' is NULL or missing - defaulting to 0")
        return 0
    try:
        number = float(value)
        if not math.isfinite(number):
            print(f"[WARN] '{field_name}' is not finite - defaulting to 0")
            return 0
        return number
    except (ValueError, TypeError):
        print(f"[WARN] Invalid value for '{field_name}': {value!r} - defaulting to 0")
        return 0

# ============================================================
# SAVE ONE SENSOR MESSAGE TO CSV
# ============================================================

def save_sensor_data(data):
    device    = data.get("device_id", "UNKNOWN")
    temp      = get_sensor_value(data, "temperature_2")   # air temperature
    soil_temp = get_sensor_value(data, "temperature_1")   # soil temperature
    humidity  = get_sensor_value(data, "humidity")
    light     = get_sensor_value(data, "light_lux")
    soil      = get_sensor_value(data, "soil_moisture")

    timestamp = datetime.now().strftime("%Y-%m-%d | %H:%M:%S")
    row = [timestamp, device, temp, humidity, light, soil, soil_temp]

    try:
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        print(f"[OK] Row written to CSV: {row}")
        trim_csv()
    except (OSError, csv.Error) as e:
        print(f"[ERROR] Failed to write CSV: {e}")

# ============================================================
# MQTT CALLBACKS
# ============================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Connected to LOCAL Mosquitto broker.")
        result, _ = client.subscribe(SENSOR_TOPIC, qos=1)
        if result == mqtt.MQTT_ERR_SUCCESS:
            print(f"[MQTT] Subscribed to: {SENSOR_TOPIC}")
        else:
            print(f"[ERROR] Subscribe failed: {result}")
    else:
        print(f"[MQTT ERROR] Connection failed. Code: {rc}")

def on_disconnect(client, userdata, rc):
    if rc != 0:
        print("[MQTT WARN] Local MQTT connection lost. Paho will reconnect.")
    else:
        print("[MQTT] Disconnected from local broker.")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
        if not isinstance(data, dict):
            print("[ERROR] Payload must be a JSON object.")
            return
        print(f"[MQTT] Message received on {msg.topic}")
        save_sensor_data(data)
    except UnicodeDecodeError as e:
        print(f"[ERROR] Invalid UTF-8: {e}")
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}")
    except Exception as e:
        print(f"[ERROR] Could not process message: {e}")

# ============================================================
# CREATE MQTT CLIENT
# ============================================================

def create_mqtt_client():
    try:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=CLIENT_ID,
            protocol=mqtt.MQTTv311
        )
    except (AttributeError, TypeError):
        client = mqtt.Client(
            client_id=CLIENT_ID,
            protocol=mqtt.MQTTv311
        )
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client

# ============================================================
# MAIN
# ============================================================

def main():
    ensure_csv()
    client = create_mqtt_client()

    print("============================================")
    print(" SmartGreen - Local CSV Sensor Logger")
    print("============================================")
    print(f"[CONFIG] Broker : {LOCAL_BROKER}:{LOCAL_PORT}")
    print(f"[CONFIG] Topic  : {SENSOR_TOPIC}")
    print(f"[CONFIG] CSV    : {CSV_FILE}")
    print("[INFO] No Internet required for this script.")
    print("============================================")

    while True:
        try:
            print(f"[MQTT] Connecting to local broker {LOCAL_BROKER}:{LOCAL_PORT}...")
            client.connect(LOCAL_BROKER, LOCAL_PORT, keepalive=60)
            client.loop_forever(retry_first_connection=True)
        except TypeError:
            try:
                client.connect(LOCAL_BROKER, LOCAL_PORT, keepalive=60)
                client.loop_forever()
            except Exception as e:
                print(f"[ERROR] MQTT connection failed: {e}")
        except KeyboardInterrupt:
            print("\n[INFO] Logger stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            print("[INFO] Retrying in 5 seconds...")
            time.sleep(5)

    client.disconnect()

if __name__ == "__main__":
    main()