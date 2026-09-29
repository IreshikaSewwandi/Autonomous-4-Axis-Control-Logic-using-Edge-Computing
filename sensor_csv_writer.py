
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

# CSV output file
CSV_FILE = "green_house/sensor_data.csv"

# Keep only the latest 1000 data rows
MAX_ROWS = 1000

# Local Mosquitto broker running on the Edge device
# If Mosquitto is installed on this same Orange Pi/PC, use 127.0.0.1
LOCAL_BROKER = "127.0.0.1"
LOCAL_PORT = 1883

# Topic published by the hybrid ESP32 code
SENSOR_TOPIC = "greenhouse/local/telemetry"

CLIENT_ID = "greenhouse_csv_logger"


CSV_HEADER = [
    "timestamp",
    "device",
    "temp",
    "humidity",
    "light",
    "soil_moisture",
    "soil_temp"
]


# ============================================================
# CREATE CSV FILE IF IT DOES NOT EXIST
# ============================================================

def ensure_csv():
    folder = os.path.dirname(CSV_FILE)

    if folder:
        os.makedirs(folder, exist_ok=True)

    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(CSV_HEADER)

        print(f"[INFO] CSV file ready: {CSV_FILE}")


# ============================================================
# KEEP LATEST 1000 DATA ROWS
# ============================================================

def trim_csv():
    try:
        with open(CSV_FILE, "r", newline="", encoding="utf-8") as file:
            rows = list(csv.reader(file))

        if not rows:
            ensure_csv()
            return

        header = rows[0]
        data_rows = rows[1:]

        if len(data_rows) > MAX_ROWS:
            data_rows = data_rows[-MAX_ROWS:]

            temp_file = CSV_FILE + ".tmp"

            with open(
                temp_file, "w", newline="", encoding="utf-8"
            ) as file:
                writer = csv.writer(file)
                writer.writerow(header)
                writer.writerows(data_rows)
                file.flush()
                os.fsync(file.fileno())

            os.replace(temp_file, CSV_FILE)

            print(f"[INFO] CSV trimmed to latest {MAX_ROWS} rows.")

    except (OSError, csv.Error) as error:
        print(f"[WARN] CSV trimming failed: {error}")


# ============================================================
# VALIDATE SENSOR VALUES
# Missing, null, invalid or non-finite values become 0
# ============================================================

def get_sensor_value(data, field_name):
    value = data.get(field_name)

    if value is None or isinstance(value, bool):
        print(
            f"[WARN] Sensor field '{field_name}' is NULL or missing. "
            "Defaulting to 0."
        )
        return 0

    try:
        number = float(value)

        if not math.isfinite(number):
            print(
                f"[WARN] Sensor field '{field_name}' is not finite. "
                "Defaulting to 0."
            )
            return 0

        return number

    except (ValueError, TypeError):
        print(
            f"[WARN] Invalid value for '{field_name}': {value!r}. "
            "Defaulting to 0."
        )
        return 0


# ============================================================
# SAVE ONE SENSOR MESSAGE TO CSV
# ============================================================

def save_sensor_data(data):
    device = data.get("device_id", "UNKNOWN")

    # Match the JSON field names published by the hybrid ESP32
    temp = get_sensor_value(data, "temperature_2")
    soil_temp = get_sensor_value(data, "temperature_1")
    humidity = get_sensor_value(data, "humidity")
    light = get_sensor_value(data, "light_lux")
    soil_moisture = get_sensor_value(data, "soil_moisture")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    row = [
        timestamp,
        device,
        temp,
        humidity,
        light,
        soil_moisture,
        soil_temp
    ]

    try:
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(row)
            file.flush()
            os.fsync(file.fileno())

        print(f"[OK] Sensor data saved: {row}")

        trim_csv()

    except (OSError, csv.Error) as error:
        print(f"[ERROR] Failed to write CSV: {error}")


# ============================================================
# MQTT CALLBACKS
# ============================================================

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[MQTT] Connected to LOCAL Mosquitto broker.")
        print(f"[MQTT] Subscribing to: {SENSOR_TOPIC}")

        result, mid = client.subscribe(SENSOR_TOPIC, qos=1)

        if result == mqtt.MQTT_ERR_SUCCESS:
            print("[MQTT] Subscription request sent.")
        else:
            print(f"[ERROR] Subscribe request failed: {result}")

    else:
        print(f"[MQTT ERROR] Local broker connection failed. Code: {rc}")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(
            "[MQTT WARN] Local MQTT connection lost. "
            "Paho will attempt to reconnect."
        )
    else:
        print("[MQTT] Disconnected from local broker.")


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)

        if not isinstance(data, dict):
            print("[ERROR] MQTT payload must be a JSON object.")
            return

        print(f"[MQTT] Received message on {msg.topic}")

        save_sensor_data(data)

    except UnicodeDecodeError as error:
        print(f"[ERROR] Message is not valid UTF-8: {error}")

    except json.JSONDecodeError as error:
        print(f"[ERROR] Invalid JSON received: {error}")

    except Exception as error:
        print(f"[ERROR] Could not process MQTT message: {error}")


# ============================================================
# CREATE MQTT CLIENT (Paho MQTT 1.x / 2.x COMPATIBILITY)
# ============================================================

def create_mqtt_client():
    try:
        # Paho MQTT 2.x
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=CLIENT_ID,
            protocol=mqtt.MQTTv311
        )

    except (AttributeError, TypeError):
        # Older Paho MQTT versions
        client = mqtt.Client(
            client_id=CLIENT_ID,
            protocol=mqtt.MQTTv311
        )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Retry reconnecting if the local broker temporarily disconnects
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    return client


# ============================================================
# MAIN
# ============================================================

def main():
    ensure_csv()

    client = create_mqtt_client()

    print("============================================")
    print(" Smart Greenhouse - Local CSV Sensor Logger")
    print("============================================")
    print(f"[CONFIG] Broker: {LOCAL_BROKER}:{LOCAL_PORT}")
    print(f"[CONFIG] Topic:  {SENSOR_TOPIC}")
    print(f"[CONFIG] CSV:    {CSV_FILE}")
    print("[INFO] Logging does not require Internet access.")
    print("[INFO] Local Wi-Fi, Edge device and broker must remain ON.")
    print("============================================")

    while True:
        try:
            print(
                f"[MQTT] Connecting to local broker "
                f"{LOCAL_BROKER}:{LOCAL_PORT}..."
            )

            client.connect(
                LOCAL_BROKER,
                LOCAL_PORT,
                keepalive=60
            )

            # Handles incoming messages and automatic reconnection
            client.loop_forever(retry_first_connection=True)

        except TypeError:
            # Compatibility fallback for older Paho versions
            try:
                client.connect(
                    LOCAL_BROKER,
                    LOCAL_PORT,
                    keepalive=60
                )
                client.loop_forever()

            except Exception as error:
                print(f"[ERROR] MQTT connection failed: {error}")

        except KeyboardInterrupt:
            print("\n[INFO] Logger stopped by user.")
            break

        except Exception as error:
            print(f"[ERROR] MQTT logger error: {error}")
            print("[INFO] Retrying in 5 seconds...")
            time.sleep(5)

    client.disconnect()


if __name__ == "__main__":
    main()