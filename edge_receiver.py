import csv
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
# FIRESTORE SETUP
# ============================================================

db = firestore.Client.from_service_account_json(
    FIREBASE_KEY_FILE
)

print("[FIRESTORE] Connected to Firestore project.")

# ============================================================
# DEFAULT THRESHOLDS
# ============================================================

SOIL_START = 40.0
SOIL_STOP  = 65.0

TEMP_START = 32.0
TEMP_STOP  = 28.0

HUM_START = 60.0
HUM_STOP  = 80.0

SHADE_ON  = 500.0
SHADE_OFF = 200.0

LED_ON  = 800.0
LED_OFF = 1000.0

# ============================================================
# FIRESTORE CONFIGURATION LISTENER
# ============================================================

def fetch_admin_config():

    global SOIL_START, SOIL_STOP
    global TEMP_START, TEMP_STOP
    global HUM_START, HUM_STOP
    global SHADE_ON, SHADE_OFF
    global LED_ON, LED_OFF

    doc_ref = db.collection("configuration").document("settings")

    def on_snapshot(doc_snapshot, changes, read_time):

        global SOIL_START, SOIL_STOP
        global TEMP_START, TEMP_STOP
        global HUM_START, HUM_STOP
        global SHADE_ON, SHADE_OFF
        global LED_ON, LED_OFF

        for doc in doc_snapshot:

            if not doc.exists:
                continue

            data = doc.to_dict()

            t = data.get("targetTemp")
            h = data.get("targetHumid")
            m = data.get("targetMoist")
            l = data.get("targetLight")

            if t is not None:
                TEMP_START = float(t)
                TEMP_STOP = float(t) - 4.0

            if h is not None:
                HUM_START = float(h) - 10.0
                HUM_STOP = float(h) + 10.0

            if m is not None:
                SOIL_START = float(m)
                SOIL_STOP = float(m) + 25.0

            if l is not None:
                SHADE_ON = float(l) + 200.0
                SHADE_OFF = float(l)

                LED_ON = float(l) - 300.0
                LED_OFF = float(l)

            print(
                f"[FIRESTORE] Admin config updated -> "
                f"TEMP={TEMP_START}/{TEMP_STOP} "
                f"HUM={HUM_START}/{HUM_STOP} "
                f"SOIL={SOIL_START}/{SOIL_STOP} "
                f"SHADE={SHADE_ON}/{SHADE_OFF} "
                f"LED={LED_ON}/{LED_OFF}"
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

    try:

        timestamp = data.get(
            "timestamp",
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        device = data.get(
            "device_id",
            "UNKNOWN"
        )

        temp = float(
            data["temperature_2"]
        )

        hum = float(
            data["humidity"]
        )

        light = float(
            data["light_lux"]
        )

        soil = float(
            data["soil_moisture"]
        )

        soil_temp = float(
            data["temperature_1"]
        )

    except (
        KeyError,
        TypeError,
        ValueError
    ) as e:

        print(
            f"[WARN] Invalid MQTT telemetry, "
            f"skipping: {e}"
        )

        time.sleep(5)

        continue
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

        # Pump
        if soil < SOIL_START:

            pump = "ON"

        elif soil > SOIL_STOP:

            pump = "OFF"

        # Fan
        if temp > TEMP_START:

            fan = "ON"

        elif temp < TEMP_STOP:

            fan = "OFF"

        # Mister
        if hum < HUM_START:

            mister = "ON"

        elif hum > HUM_STOP:

            mister = "OFF"

        # Shade
        if light > SHADE_ON:

            shade = "ON"

        elif light < SHADE_OFF:

            shade = "OFF"

        # LED
        if light < LED_ON:

            led = "ON"

        elif light > LED_OFF:

            led = "OFF"

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
    print(
        f"Air Temp : {temp}°C "
        f"→ Fan: {fan} "
        f"(Target: {TEMP_START}°C)"
    )

    print(
        f"Soil Temp: {soil_temp}°C"
    )

    print(
        f"Humidity : {hum}% "
        f"→ Mister: {mister}"
    )

    print(
        f"Light    : {light} Lux "
        f"→ Shade: {shade} | LED: {led}"
    )

    print(
        f"Soil     : {soil}% "
        f"→ Pump: {pump}"
    )
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