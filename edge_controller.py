import csv
import time

CSV_FILE = "green_house/sensor_data.csv"
LOG_FILE = "ActuatorLog_Local.txt"

# ================= THRESHOLDS ================= #
# Soil Moisture (%)
SOIL_START = 40.0
SOIL_STOP  = 65.0

# Temperature (°C)
TEMP_START = 32.0
TEMP_STOP  = 28.0

# Humidity (%)
HUM_START = 60.0
HUM_STOP  = 80.0

# Light (Lux)
SHADE_ON  = 50000
SHADE_OFF = 20000

LED_ON  = 800
LED_OFF = 1000

# ================= ACTUATOR STATES ================= #
pump = "OFF"
fan = "OFF"
mister = "OFF"
shade = "OFF"
led = "OFF"

# ================= PREVIOUS STATE (for change detection) ================= #
prev_state = None   # holds (pump, fan, mister, shade, led) of last logged cycle

# ================= READ LAST CSV ROW ================= #
def read_last_row():
    with open(CSV_FILE, "r") as file:
        reader = csv.reader(file)
        rows = list(reader)
        if len(rows) < 2:
            return None
        return rows[-1]

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
        if soil < SOIL_START:
            pump = "ON"
        elif soil > SOIL_STOP:
            pump = "OFF"

        # ========== TEMP → FAN ==========
        if temp > TEMP_START:
            fan = "ON"
        elif temp < TEMP_STOP:
            fan = "OFF"

        # ========== HUMIDITY → MISTER ==========
        if hum < HUM_START:
            mister = "ON"
        elif hum > HUM_STOP:
            mister = "OFF"

        # ========== LIGHT → SHADE ==========
        if light > SHADE_ON:
            shade = "ON"
        elif light < SHADE_OFF:
            shade = "OFF"

        # ========== LIGHT → LED ==========
        if light < LED_ON:
            led = "ON"
        elif light > LED_OFF:
            led = "OFF"

        # ========== DISPLAY (every cycle - console only) ==========
        print("\n EDGE AUTOMATION STATUS")
        print("Time:", timestamp)
        print("Device:", device)
        print(f"Temp: {temp}°C → Fan: {fan}")
        print(f"Humidity: {hum}% → Mister: {mister}")
        print(f"Light: {light} Lux → Shade: {shade} | LED: {led}")
        print(f"Soil: {soil}% → Pump: {pump}")

        # ========== LOG ONLY ON STATE CHANGE ========== #
        current_state = (pump, fan, mister, shade, led)

        if current_state != prev_state:
            with open(LOG_FILE, "a") as f:
                f.write(
                    f"{timestamp} | {device} | "
                    f"Temp={temp} Fan={fan} | "
                    f"Hum={hum} Mister={mister} | "
                    f"Light={light} Shade={shade} LED={led} | "
                    f"Soil={soil} Pump={pump}\n"
                )
            print("[LOG] State changed - written to ActuatorLog_Local.txt")
            prev_state = current_state
        else:
            print("[LOG] No state change - skipped log write")

    time.sleep(5)
