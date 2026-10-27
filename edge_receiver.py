import csv
import math
import time
import json
import ssl
import re
import os
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt
from google.cloud import firestore
from dotenv import load_dotenv

# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

CSV_FILE          = "green_house/sensor_data.csv"
LOG_FILE          = "ActuatorLog_Local.txt"
FIREBASE_KEY_FILE = "serviceAccountKey.json"

# ============================================================
# LOCAL MQTT (PRIMARY)
# ============================================================

# Edge PC itself uses localhost.
# ESP32 must use the Edge PC's LAN IP.
LOCAL_BROKER = "127.0.0.1"
LOCAL_PORT   = 1883

LOCAL_CONTROL_TOPIC = "greenhouse/local/control"
LOCAL_SENSOR_TOPIC  = "greenhouse/local/telemetry"

# ============================================================
# CLOUD MQTT (BACKUP / MOBILE APP)
# ============================================================

CLOUD_BROKER = os.getenv("CLOUD_BROKER")
CLOUD_PORT   = int(os.getenv("CLOUD_PORT", "8883"))
CLOUD_USER   = os.getenv("CLOUD_USER")
CLOUD_PASS   = os.getenv("CLOUD_PASS")

CLOUD_TELEMETRY_TOPIC = "greenhouse/telemetry"
CLOUD_CONTROL_TOPIC   = "greenhouse/control"

MODE_TOPIC      = "greenhouse/mode"
MANUAL_CMD_TOPIC = "greenhouse/manual_cmd"

DEFAULT_MANUAL_DURATION_MIN = 30

# ============================================================
# ENVIRONMENT VALIDATION
# ============================================================

if not CLOUD_BROKER:
    raise RuntimeError("CLOUD_BROKER is missing from .env")

if not CLOUD_USER:
    raise RuntimeError("CLOUD_USER is missing from .env")

if not CLOUD_PASS:
    raise RuntimeError("CLOUD_PASS is missing from .env")

# ============================================================
# SENSOR / VALUE HELPERS (null-safe)
# ============================================================

def read_value(data, key):
    """float return karanawa. Missing / null / invalid nam None."""
    v = data.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def show(v, unit=""):
    return "N/A" if v is None else f"{v}{unit}"


# ============================================================
# FIRESTORE SETUP
# ============================================================

db = firestore.Client.from_service_account_json(
    FIREBASE_KEY_FILE
)

print("[FIRESTORE] Connected to Firestore project.")

# ============================================================
# TARGETS (set by Admin in the mobile app -> Firestore)
# Defaults are used only until the first Firestore snapshot arrives.
# ============================================================

TARGET_TEMP      = 30.0      # Air Temp      -> Fan
TARGET_HUMID     = 75.0      # Humidity      -> Mister
TARGET_MOIST     = 60.0      # Soil Moisture -> Pump
TARGET_LIGHT     = 10000.0   # Light         -> LED / Shade
TARGET_SOIL_TEMP = 25.0      # Soil Temp     -> Pump safety gate (max)

# Small "dead-band" so actuators do not flicker ON/OFF every 5 s
# when a reading hovers exactly around the target. Set to 0 for exact switching.
TEMP_BAND      = 1.0     # deg C
HUM_BAND       = 2.0     # %
MOIST_BAND     = 3.0     # %
LIGHT_BAND_PCT = 0.05    # 5 % of target light

# Pump is blocked while soil temp is ABOVE the admin's Soil Temp target
USE_SOIL_TEMP_GATE = True

# ============================================================
# FIRESTORE CONFIGURATION LISTENER
# ============================================================

def fetch_admin_config():

    doc_ref = db.collection("configuration").document("settings")

    def on_snapshot(doc_snapshot, changes, read_time):

        global TARGET_TEMP, TARGET_HUMID, TARGET_MOIST
        global TARGET_LIGHT, TARGET_SOIL_TEMP

        for doc in doc_snapshot:

            if not doc.exists:
                continue

            data = doc.to_dict() or {}

            t  = read_value(data, "targetTemp")
            h  = read_value(data, "targetHumid")
            m  = read_value(data, "targetMoist")
            l  = read_value(data, "targetLight")
            st = read_value(data, "targetSoilTemp")

            if t  is not None: TARGET_TEMP      = t
            if h  is not None: TARGET_HUMID     = h
            if m  is not None: TARGET_MOIST     = m
            if l  is not None: TARGET_LIGHT     = l
            if st is not None: TARGET_SOIL_TEMP = st

            print(
                f"[FIRESTORE] Admin config updated -> "
                f"AIR_TEMP={TARGET_TEMP} "
                f"HUMIDITY={TARGET_HUMID} "
                f"SOIL_MOISTURE={TARGET_MOIST} "
                f"LIGHT={TARGET_LIGHT} "
                f"SOIL_TEMP={TARGET_SOIL_TEMP}"
            )

    doc_ref.on_snapshot(on_snapshot)


fetch_admin_config()

# ============================================================
# GLOBAL STATE
# ============================================================

mode = "AUTO"

manual_command = {
    "pump": "OFF",
    "fan": "OFF",
    "mister": "OFF",
    "shade": "OFF",
    "led": "OFF"
}

manual_expiry_time = None

# ============================================================
# REAL-TIME SENSOR DATA
# ============================================================

latest_sensor_data = None
last_sensor_received = 0

# ============================================================
# MQTT — LOCAL CLIENT
# ============================================================

local_client = mqtt.Client(
    client_id="edge_local"
)

local_client.reconnect_delay_set(
    min_delay=1,
    max_delay=10
)

# ============================================================
# LOCAL MQTT CONNECT
# ============================================================

def local_on_connect(
    client,
    userdata,
    flags,
    rc
):

    if rc == 0:

        print(
            "[LOCAL MQTT] Connected to local Mosquitto broker."
        )

        result, _ = client.subscribe(
            LOCAL_SENSOR_TOPIC,
            qos=1
        )

        if result == mqtt.MQTT_ERR_SUCCESS:

            print(
                f"[LOCAL MQTT] Subscribed to: "
                f"{LOCAL_SENSOR_TOPIC}"
            )

        else:

            print(
                f"[LOCAL MQTT] Subscribe failed: "
                f"{result}"
            )

    else:

        print(
            f"[LOCAL MQTT] Connection failed. rc={rc}"
        )

# ============================================================
# LOCAL MQTT TELEMETRY MESSAGE
# ============================================================

def local_on_message(
    client,
    userdata,
    msg
):

    global latest_sensor_data
    global last_sensor_received

    try:

        data = json.loads(
            msg.payload.decode("utf-8")
        )

        if not isinstance(data, dict):

            print(
                "[LOCAL MQTT] Telemetry payload "
                "must be a JSON object."
            )

            return

        latest_sensor_data = data
        last_sensor_received = time.time()

        print(
            f"[LOCAL MQTT] Telemetry received | "
            f"Temp={data.get('temperature_2')}°C | "
            f"SoilTemp={data.get('temperature_1')}°C | "
            f"Humidity={data.get('humidity')}% | "
            f"Soil={data.get('soil_moisture')}% | "
            f"Light={data.get('light_lux')} Lux"
        )

    except UnicodeDecodeError as e:

        print(
            f"[LOCAL MQTT] Invalid UTF-8: {e}"
        )

    except json.JSONDecodeError as e:

        print(
            f"[LOCAL MQTT] Invalid JSON: {e}"
        )

    except Exception as e:

        print(
            f"[LOCAL MQTT] Could not process "
            f"telemetry: {e}"
        )

local_client.on_connect = local_on_connect
local_client.on_message = local_on_message

# ============================================================
# START LOCAL MQTT
# ============================================================

try:

    local_client.connect(
        LOCAL_BROKER,
        LOCAL_PORT,
        keepalive=60
    )

    local_client.loop_start()

    print(
        f"[LOCAL MQTT] Connecting to "
        f"{LOCAL_BROKER}:{LOCAL_PORT}..."
    )

except Exception as e:

    print(
        f"[LOCAL MQTT] Could not connect: {e} "
        f"— local control unavailable."
    )

# ============================================================
# MQTT — CLOUD CLIENT
# ============================================================

def cloud_on_connect(
    client,
    userdata,
    flags,
    rc
):

    print(
        f"[CLOUD MQTT] Connected. rc={rc}"
    )

    if rc == 0:

        client.subscribe(
            MODE_TOPIC,
            qos=1
        )

        client.subscribe(
            MANUAL_CMD_TOPIC,
            qos=1
        )

        print(
            f"[CLOUD MQTT] Subscribed to "
            f"{MODE_TOPIC} and {MANUAL_CMD_TOPIC}"
        )

# ============================================================
# CLOUD MQTT MESSAGE
# ============================================================

def cloud_on_message(
    client,
    userdata,
    msg
):

    global mode
    global manual_command
    global manual_expiry_time

    try:

        payload = json.loads(
            msg.payload.decode("utf-8")
        )

    except Exception as e:

        print(
            f"[CLOUD MQTT] Could not parse message: {e}"
        )

        return

    # ---------------- MODE ----------------

    if msg.topic == MODE_TOPIC:

        new_mode = payload.get(
            "mode",
            ""
        ).upper()

        if new_mode == "MANUAL":

            duration_min = payload.get(
                "duration_minutes",
                DEFAULT_MANUAL_DURATION_MIN
            )

            try:

                duration_min = float(
                    duration_min
                )

            except (
                TypeError,
                ValueError
            ):

                duration_min = (
                    DEFAULT_MANUAL_DURATION_MIN
                )
            mode = "MANUAL"
            
            manual_command = {
                "pump": "OFF",
                "fan": "OFF",
                "mister": "OFF",
                "shade": "OFF",
                "led": "OFF"
            }

            manual_expiry_time = (
                datetime.now()
                + timedelta(minutes=duration_min)
            )

            print(
                f"[MODE] MANUAL for "
                f"{duration_min} min "
                f"(auto-revert at "
                f"{manual_expiry_time.strftime('%H:%M:%S')})"
            )

        elif new_mode == "AUTO":

            mode = "AUTO"
            manual_expiry_time = None

            print(
                "[MODE] Switched to AUTO"
            )

    # ---------------- MANUAL COMMAND ----------------

    elif msg.topic == MANUAL_CMD_TOPIC:

        for key in manual_command:

            if key in payload:

                manual_command[key] = payload[key]

        print(
            f"[MANUAL] Command received: "
            f"{payload}"
        )


cloud_client = mqtt.Client(
    client_id="edge_cloud"
)

cloud_client.username_pw_set(
    CLOUD_USER,
    CLOUD_PASS
)

# Current development configuration.
# Certificate verification can be hardened later.
cloud_client.tls_set(
    cert_reqs=ssl.CERT_NONE
)

cloud_client.tls_insecure_set(
    True
)

cloud_client.on_connect = cloud_on_connect
cloud_client.on_message = cloud_on_message

print(
    f"[CLOUD MQTT] Connecting to "
    f"{CLOUD_BROKER}:{CLOUD_PORT}..."
)

cloud_client.connect(
    CLOUD_BROKER,
    CLOUD_PORT,
    60
)
cloud_client.loop_start()

# ============================================================
# ACTUATOR STATES
# ============================================================

pump = "OFF"
fan = "OFF"
mister = "OFF"
shade = "OFF"
led = "OFF"

# ============================================================
# RESTORE PREVIOUS STATE
# ============================================================

def load_last_state():

    try:

        with open(
            LOG_FILE,
            "r"
        ) as f:

            lines = f.readlines()

            if not lines:
                return None

            last = lines[-1].strip()

            pm = re.search(
                r"Pump=(\w+)",
                last
            )

            fm = re.search(
                r"Fan=(\w+)",
                last
            )

            mm = re.search(
                r"Mister=(\w+)",
                last
            )

            sm = re.search(
                r"Shade=(\w+)",
                last
            )

            lm = re.search(
                r"LED=(\w+)",
                last
            )

            if all([
                pm,
                fm,
                mm,
                sm,
                lm
            ]):

                return (
                    pm.group(1),
                    fm.group(1),
                    mm.group(1),
                    sm.group(1),
                    lm.group(1)
                )

    except FileNotFoundError:

        pass

    return None

prev_state = load_last_state()

if prev_state:

    print(
        f"[INFO] Restored previous state: "
        f"{prev_state}"
    )

else:

    print(
        "[INFO] No previous state — starting fresh."
    )
# ============================================================
# PUBLISH CONTROL
# ============================================================

def publish_control(payload_dict):

    msg = json.dumps(
        payload_dict
    )

    # PRIMARY — LOCAL MQTT

    try:

        if local_client.is_connected():

            result = local_client.publish(
                LOCAL_CONTROL_TOPIC,
                msg,
                qos=1
            )

            if result.rc != mqtt.MQTT_ERR_SUCCESS:

                print(
                    f"[LOCAL MQTT] Publish failed: "
                    f"{result.rc}"
                )

    except Exception as e:

        print(
            f"[LOCAL MQTT] Publish failed: {e}"
        )

    # BACKUP — CLOUD MQTT

    try:

        if cloud_client.is_connected():

            result = cloud_client.publish(
                CLOUD_CONTROL_TOPIC,
                msg,
                qos=1
            )

            if result.rc != mqtt.MQTT_ERR_SUCCESS:

                print(
                    f"[CLOUD MQTT] Publish failed: "
                    f"{result.rc}"
                )

    except Exception as e:

        print(
            f"[CLOUD MQTT] Publish failed: {e}"
        )
# ============================================================
# PUBLISH CLOUD TELEMETRY
# ============================================================

def publish_telemetry(payload_dict):

    try:

        if cloud_client.is_connected():

            result = cloud_client.publish(
                CLOUD_TELEMETRY_TOPIC,
                json.dumps(payload_dict),
                qos=1
            )

            if result.rc == mqtt.MQTT_ERR_SUCCESS:

                print(
                    f"[OK] Telemetry published to "
                    f"'{CLOUD_TELEMETRY_TOPIC}'"
                )

            else:

                print(
                    f"[CLOUD MQTT] Telemetry publish "
                    f"failed: {result.rc}"
                )

    except Exception as e:

        print(
            f"[CLOUD MQTT] Telemetry publish failed: {e}"
        )

# ============================================================
# MAIN CONTROL LOOP
# ============================================================

while True:

    # --------------------------------------------------------
    # SAFETY: AUTO-REVERT MANUAL MODE
    # --------------------------------------------------------

    if (
        mode == "MANUAL"
        and manual_expiry_time is not None
    ):

        if datetime.now() >= manual_expiry_time:

            mode = "AUTO"
            manual_expiry_time = None

            print(
                "[SAFETY] Manual mode expired "
                "- reverted to AUTO"
            )

    # --------------------------------------------------------
    # WAIT FOR MQTT SENSOR DATA
    # --------------------------------------------------------

    data = latest_sensor_data

    if data is None:

        print(
            "[WAIT] No local sensor telemetry received yet."
        )

        time.sleep(5)

        continue

    # --------------------------------------------------------
    # SENSOR VALUES
    # --------------------------------------------------------

    timestamp = data.get(
        "timestamp",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    device = data.get("device_id", "UNKNOWN")

    temp      = read_value(data, "temperature_2")   # air temp
    hum       = read_value(data, "humidity")
    light     = read_value(data, "light_lux")
    soil      = read_value(data, "soil_moisture")
    soil_temp = read_value(data, "temperature_1")   # soil temp

    # --------------------------------------------------------
    # CHECK SENSOR DATA FRESHNESS
    # --------------------------------------------------------

    sensor_age = (
        time.time()
        - last_sensor_received
    )

    if sensor_age > 15:

        print(
            f"[WARN] Sensor telemetry is "
            f"{sensor_age:.1f}s old."
        )
    # --------------------------------------------------------
    # MODE DISPLAY
    # --------------------------------------------------------

    mode_display = mode

    if (
        mode == "MANUAL"
        and manual_expiry_time is not None
    ):

        remaining = (
            manual_expiry_time
            - datetime.now()
        ).total_seconds() / 60

        mode_display = (
            f"MANUAL "
            f"({max(0, round(remaining))} min left)"
        )

    print(
        f"\n--- Mode: {mode_display} ---"
    )

    # --------------------------------------------------------
    # AUTO CONTROL
    # --------------------------------------------------------

    if mode == "AUTO":

        # Fan  : Air Temp  > target -> ON
        if temp is None:
            fan = "OFF"
        elif temp > TARGET_TEMP:
            fan = "ON"
        elif temp < TARGET_TEMP - TEMP_BAND:
            fan = "OFF"

        # Mister : Humidity < target -> ON
        if hum is None:
            mister = "OFF"
        elif hum < TARGET_HUMID:
            mister = "ON"
        elif hum > TARGET_HUMID + HUM_BAND:
            mister = "OFF"

        # Pump : Soil Moisture < target -> ON
        #        (blocked while Soil Temp is above its target)
        soil_temp_hot = (
            USE_SOIL_TEMP_GATE
            and soil_temp is not None
            and soil_temp > TARGET_SOIL_TEMP
        )

        if soil is None or soil_temp_hot:
            pump = "OFF"
        elif soil < TARGET_MOIST:
            pump = "ON"
        elif soil > TARGET_MOIST + MOIST_BAND:
            pump = "OFF"

        # LED + Shade : Light vs target
        #   LED   ON  : light < target        OFF : light > target + band
        #   Shade ON  : light > target + band OFF : light < target
        if light is None:
            led = "OFF"
            shade = "OFF"
        else:
            light_band = TARGET_LIGHT * LIGHT_BAND_PCT

            if light < TARGET_LIGHT:
                led = "ON"
            elif light > TARGET_LIGHT + light_band:
                led = "OFF"

            if light > TARGET_LIGHT + light_band:
                shade = "ON"
            elif light < TARGET_LIGHT:
                shade = "OFF"

        actions = {
            "pump": pump,
            "fan": fan,
            "mister": mister,
            "shade": shade,
            "led": led,
            "mode": "AUTO"
        }
    # --------------------------------------------------------
    # MANUAL CONTROL
    # --------------------------------------------------------
    else:
        pump = manual_command["pump"]
        fan = manual_command["fan"]
        mister = manual_command["mister"]
        shade = manual_command["shade"]
        led = manual_command["led"]

        actions = {
            "pump": pump,
            "fan": fan,
            "mister": mister,
            "shade": shade,
            "led": led,
            "mode": "MANUAL"
        }
    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------
    print(f"Air Temp : {show(temp, '°C')} → Fan: {fan} (Target: {TARGET_TEMP}°C)")
    print(f"Soil Temp: {show(soil_temp, '°C')} (Pump allowed up to: {TARGET_SOIL_TEMP}°C)")
    print(f"Humidity : {show(hum, '%')} → Mister: {mister} (Target: {TARGET_HUMID}%)")
    print(f"Light    : {show(light, ' Lux')} → Shade: {shade} | LED: {led} (Target: {TARGET_LIGHT} Lux)")
    print(f"Soil     : {show(soil, '%')} → Pump: {pump} (Target: {TARGET_MOIST}%)")

    # --------------------------------------------------------
    # SEND CONTROL
    # --------------------------------------------------------
    publish_control(
        actions
    )

    # --------------------------------------------------------
    # CLOUD TELEMETRY
    # --------------------------------------------------------

    publish_telemetry({

        "device_id": device,

        "temperature_1": soil_temp,
        "temperature_2": temp,

        "humidity": hum,

        "soil_moisture": soil,

        "light_lux": light,

        "pump": pump,
        "fan": fan,
        "mister": mister,
        "shade": shade,
        "led": led,

        "mode": mode,

        "timestamp": timestamp
    })
    # --------------------------------------------------------
    # ACTUATOR LOG
    # --------------------------------------------------------
    current_state = (
        pump,
        fan,
        mister,
        shade,
        led
    )

    if current_state != prev_state:

        with open(
            LOG_FILE,
            "a"
        ) as f:

            f.write(
                f"{timestamp} | {device} | "
                f"Mode={mode} | "
                f"Temp={temp} Fan={fan} | "
                f"Hum={hum} Mister={mister} | "
                f"Light={light} Shade={shade} "
                f"LED={led} | "
                f"Soil={soil} Pump={pump} | "
                f"soil_temp={soil_temp}\n"
            )

        print(
            "[LOG] State changed — "
            "written to ActuatorLog_Local.txt"
        )

        prev_state = current_state

    else:

        print(
            "[LOG] No state change — "
            "skipped log write"
        )

    # --------------------------------------------------------
    # CONTROL INTERVAL
    # --------------------------------------------------------

    time.sleep(5)
