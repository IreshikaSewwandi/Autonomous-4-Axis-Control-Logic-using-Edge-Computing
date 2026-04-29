import csv
import time
import json
import ssl
import paho.mqtt.client as mqtt

CSV_FILE = "green_house/sensor_data.csv"
LOG_FILE = "ActuatorLog_Local.txt"

# --- MQTT CONFIGURATION ---
BROKER = "c0eacb6e24dd4984814b3e19f4daa7a4.s1.eu.hivemq.cloud"
PORT = 8883
USERNAME = "greenhouse_admin"
PASSWORD = "Admin@123"
CONTROL_TOPIC = "greenhouse/control"

# Setup MQTT Client for sending actions back to ESP32
client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)

print("Connecting to Cloud Broker to send actions...")
client.connect(BROKER, PORT, 60)
client.loop_start()

# ================= THRESHOLDS ================= #
SOIL_START = 40.0
SOIL_STOP  = 65.0
TEMP_START = 32.0
TEMP_STOP  = 28.0
HUM_START = 60.0
HUM_STOP  = 80.0
SHADE_ON  = 500
SHADE_OFF = 200
LED_ON  = 800
LED_OFF = 1000

# ================= ACTUATOR STATES ================= #
pump = "OFF"
fan = "OFF"
mister = "OFF"
shade = "OFF"
led = "OFF"

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
    row = read_last_row()

    if row and len(row) >= 6:
        timestamp = row[0]
        device = row[1]
        temp = float(row[2])
        hum  = float(row[3])
        light = float(row[4])
        soil = float(row[5])

        # ========== SOIL → PUMP ==========
        if soil < SOIL_START: pump = "ON"
        elif soil > SOIL_STOP: pump = "OFF"

        # ========== TEMP → FAN ==========
        if temp > TEMP_START: fan = "ON"
        elif temp < TEMP_STOP: fan = "OFF"

        # ========== HUMIDITY → MISTER ==========
        if hum < HUM_START: mister = "ON"
        elif hum > HUM_STOP: mister = "OFF"

        # ========== LIGHT → SHADE ==========
        if light > SHADE_ON: shade = "ON"
        elif light < SHADE_OFF: shade = "OFF"

        # ========== LIGHT → LED ==========
        if light < LED_ON: led = "ON"
        elif light > LED_OFF: led = "OFF"

        # ========== DISPLAY ==========
        print("\nEDGE AUTOMATION STATUS")
        print("Time:", timestamp)
        print(f"Temp: {temp}°C → Fan: {fan}")       
        print(f"Humidity: {hum}% → Mister: {mister}")
        print(f"Light: {light} Lux → Shade: {shade} | LED: {led}")
        print(f"Soil: {soil}% → Pump: {pump}")

        # ========== PUBLISH ACTIONS TO ESP32 ==========
        actions_payload = {
            "pump": pump,
            "fan": fan,
            "mister": mister,
            "shade": shade,
            "led": led
        }
        client.publish(CONTROL_TOPIC, json.dumps(actions_payload))

        # ========== LOG ==========
        with open(LOG_FILE, "a") as f:
            f.write(f"{timestamp} | {device} | Temp={temp} Fan={fan} | Hum={hum} Mister={mister} | Light={light} Shade={shade} LED={led} | Soil={soil} Pump={pump}\n ")

    time.sleep(5)
