import csv
import time
import json
import ssl
import re
from datetime import datetime, timedelta
import paho.mqtt.client as mqtt
from google.cloud import firestore

CSV_FILE = "green_house/sensor_data.csv"
LOG_FILE = "ActuatorLog_Local.txt"
FIREBASE_KEY_FILE = "serviceAccountKey.json"

# --- MQTT CONFIGURATION ---
BROKER = "c0eacb6e24dd4984814b3e19f4daa7a4.s1.eu.hivemq.cloud"
PORT = 8883
USERNAME = "greenhouse_admin"
PASSWORD = "Admin@123"

CONTROL_TOPIC = "greenhouse/control"        # Edge PUBLISHES final actions here (ESP32 listens) - PUBLISH ONLY
MANUAL_CMD_TOPIC = "greenhouse/manual_cmd"  # Mobile app publishes farmer commands here - Edge SUBSCRIBES
SENSOR_TOPIC = "greenhouse/telemetry"       # Edge publishes raw sensor data (mobile app dashboard)
MODE_TOPIC = "greenhouse/mode"              # Edge subscribes here for AUTO/MANUAL switch (+ duration)

DEFAULT_MANUAL_DURATION_MIN = 30  # used if the app doesn't send a duration

# ================= FIRESTORE SETUP (Admin Configuration) ================= #
db = firestore.Client.from_service_account_json(FIREBASE_KEY_FILE)
print("[FIRESTORE] Connected to Firestore project.")

SOIL_START = 40.0
SOIL_STOP  = 65.0
TEMP_START = 32.0
TEMP_STOP  = 28.0
HUM_START  = 60.0
HUM_STOP   = 80.0
SHADE_ON  = 500
SHADE_OFF = 200
LED_ON  = 800
LED_OFF = 1000

def fetch_admin_config():
    global SOIL_START, SOIL_STOP, TEMP_START, TEMP_STOP, HUM_START, HUM_STOP, SHADE_ON, SHADE_OFF, LED_ON, LED_OFF
    doc_ref = db.collection("configuration").document("settings")

    def on_snapshot(doc_snapshot, changes, read_time):
        global SOIL_START, SOIL_STOP, TEMP_START, TEMP_STOP, HUM_START, HUM_STOP, SHADE_ON, SHADE_OFF, LED_ON, LED_OFF
        for doc in doc_snapshot:
            if doc.exists:
                data = doc.to_dict()
                target_temp = data.get("targetTemp")
                target_humid = data.get("targetHumid")
                target_moist = data.get("targetMoist")
                target_light = data.get("targetLight")

                if target_temp is not None:
                    TEMP_START = float(target_temp)
                    TEMP_STOP = float(target_temp) - 4.0

                if target_humid is not None:
                    HUM_START = float(target_humid) - 10.0
                    HUM_STOP = float(target_humid) + 10.0

                if target_moist is not None:
                    SOIL_START = float(target_moist)
                    SOIL_STOP = float(target_moist) + 25.0

                if target_light is not None:
                    SHADE_ON = float(target_light) + 200.0   # too bright -> deploy shade
                    SHADE_OFF = float(target_light)
                    LED_ON = float(target_light) - 300.0     # too dark -> turn on LED
                    LED_OFF = float(target_light)

                print(f"[FIRESTORE] Admin config updated -> "
                      f"TEMP_START={TEMP_START}, TEMP_STOP={TEMP_STOP}, "
                      f"HUM_START={HUM_START}, HUM_STOP={HUM_STOP}, "
                      f"SOIL_START={SOIL_START}, SOIL_STOP={SOIL_STOP}, "
                      f"SHADE_ON={SHADE_ON}, SHADE_OFF={SHADE_OFF}, "
                      f"LED_ON={LED_ON}, LED_OFF={LED_OFF}")

    doc_ref.on_snapshot(on_snapshot)

fetch_admin_config()

# ================= GLOBAL STATE ================= #
mode = "AUTO"
manual_command = {
    "pump": "OFF",
    "fan": "OFF",
    "mister": "OFF",
    "shade": "OFF",
    "led": "OFF"
}
manual_expiry_time = None   # datetime when MANUAL mode should auto-revert to AUTO

# ================= MQTT CALLBACKS ================= #
def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected with result code {rc}")
    client.subscribe(MODE_TOPIC)
    client.subscribe(MANUAL_CMD_TOPIC)
    print(f"[MQTT] Subscribed to '{MODE_TOPIC}' and '{MANUAL_CMD_TOPIC}'")

def on_message(client, userdata, msg):
    global mode, manual_command, manual_expiry_time
    try:
        payload = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"[MQTT] Could not parse message on '{msg.topic}': {e}")
        return

    if msg.topic == MODE_TOPIC:
        new_mode = payload.get("mode", "").upper()

        if new_mode == "MANUAL":
            # Farmer can set their own duration (in minutes). Falls back to
            # the default if the app doesn't send one.
            duration_min = payload.get("duration_minutes", DEFAULT_MANUAL_DURATION_MIN)
            try:
                duration_min = float(duration_min)
            except (TypeError, ValueError):
                duration_min = DEFAULT_MANUAL_DURATION_MIN

            mode = "MANUAL"
            manual_expiry_time = datetime.now() + timedelta(minutes=duration_min)
            print(f"[MODE] Switched to MANUAL for {duration_min} minutes "
                  f"(auto-revert at {manual_expiry_time.strftime('%H:%M:%S')})")

        elif new_mode == "AUTO":
            mode = "AUTO"
            manual_expiry_time = None
            print("[MODE] Switched to AUTO")

    elif msg.topic == MANUAL_CMD_TOPIC:
        for key in manual_command:
            if key in payload:
                manual_command[key] = payload[key]
        print(f"[MANUAL] Command received: {payload}")

# ================= MQTT SETUP ================= #
client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)
client.on_connect = on_connect
client.on_message = on_message

print("Connecting to Cloud Broker...")
client.connect(BROKER, PORT, 60)
client.loop_start()

# ================= ACTUATOR STATES ================= #
pump = "OFF"
fan = "OFF"
mister = "OFF"
shade = "OFF"
led = "OFF"

# ================= LOAD PREVIOUS STATE FROM LOG (avoid duplicate on restart) ================= #
def load_last_state():
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
            if not lines:
                return None
            last_line = lines[-1].strip()
            pump_match   = re.search(r"Pump=(\w+)", last_line)
            fan_match    = re.search(r"Fan=(\w+)", last_line)
            mister_match = re.search(r"Mister=(\w+)", last_line)
            shade_match  = re.search(r"Shade=(\w+)", last_line)
            led_match    = re.search(r"LED=(\w+)", last_line)
            if all([pump_match, fan_match, mister_match, shade_match, led_match]):
                return (
                    pump_match.group(1), fan_match.group(1),
                    mister_match.group(1), shade_match.group(1), led_match.group(1)
                )
    except FileNotFoundError:
        pass
    return None

prev_state = load_last_state()
if prev_state:
    print(f"[INFO] Restored previous state from log: {prev_state}")
else:
    print("[INFO] No previous state found - starting fresh.")

# ================= READ LAST CSV ROW ================= #
def read_last_row():
    try:
        with open(CSV_FILE, "r") as file:
            reader = csv.reader(file)
            rows = list(reader)
            if len(rows) < 2:
                return None
            return rows[-1]
    except FileNotFoundError:
        return None

# ================= MAIN LOOP ================= #
while True:
    # ===== SAFETY: auto-revert MANUAL mode if its duration has expired ===== #
    if mode == "MANUAL" and manual_expiry_time is not None:
        if datetime.now() >= manual_expiry_time:
            mode = "AUTO"
            manual_expiry_time = None
            print("[SAFETY] Manual mode duration expired - automatically reverted to AUTO")

    row = read_last_row()
    if row and len(row) >= 7:
        timestamp = row[0]
        device = row[1]
        temp = float(row[2])
        hum  = float(row[3])
        light = float(row[4])
        soil = float(row[5])
        soil_temp = float(row[6])

        mode_display = mode
        if mode == "MANUAL" and manual_expiry_time is not None:
            remaining = (manual_expiry_time - datetime.now()).total_seconds() / 60
            mode_display = f"MANUAL ({max(0, round(remaining))} min left)"

        print(f"\n--- Mode: {mode_display} | Time: {timestamp} ---")

        if mode == "AUTO":
            if soil < SOIL_START: pump = "ON"
            elif soil > SOIL_STOP: pump = "OFF"

            if temp > TEMP_START: fan = "ON"
            elif temp < TEMP_STOP: fan = "OFF"

            if hum < HUM_START: mister = "ON"
            elif hum > HUM_STOP: mister = "OFF"

            if light > SHADE_ON: shade = "ON"
            elif light < SHADE_OFF: shade = "OFF"

            if light < LED_ON: led = "ON"
            elif light > LED_OFF: led = "OFF"

            actions_payload = {
                "pump": pump, "fan": fan, "mister": mister,
                "shade": shade, "led": led
            }

        else:
            pump   = manual_command["pump"]
            fan    = manual_command["fan"]
            mister = manual_command["mister"]
            shade  = manual_command["shade"]
            led    = manual_command["led"]

            actions_payload = manual_command.copy()

        print(f"Air Temp: {temp}°C → Fan: {fan}  (Target: {TEMP_START}°C)")
        print(f"Soil Temp: {soil_temp}°C")
        print(f"Humidity: {hum}% → Mister: {mister}")
        print(f"Light: {light} Lux → Shade: {shade} | LED: {led}")
        print(f"Soil: {soil}% → Pump: {pump}")

        client.publish(CONTROL_TOPIC, json.dumps(actions_payload))

        sensor_payload = {
            "temperature_1": soil_temp,
            "temperature_2": temp,
            "humidity": hum,
            "soil_moisture": soil,
            "light_lux": light,
            "timestamp": timestamp
        }
        client.publish(SENSOR_TOPIC, json.dumps(sensor_payload))
        print(f"[OK] Published sensor data to '{SENSOR_TOPIC}'")

        current_state = (pump, fan, mister, shade, led)
        if current_state != prev_state:
            with open(LOG_FILE, "a") as f:
                f.write(
                    f"{timestamp} | {device} | Mode={mode} | "
                    f"Temp={temp} Fan={fan} | "
                    f"Hum={hum} Mister={mister} | "
                    f"Light={light} Shade={shade} LED={led} | "
                    f"Soil={soil} Pump={pump} | soil_temp={soil_temp}\n"
                )
            print("[LOG] State changed - written to ActuatorLog_Local.txt")
            prev_state = current_state
        else:
            print("[LOG] No state change - skipped log write")

    time.sleep(5)
