# edge_control_system.py

# ----------------------------------------------------------------------
# FIX: Use gpiozero and Mocking for Windows compatibility
# ----------------------------------------------------------------------
from gpiozero.pins.mock import MockFactory 
from gpiozero import Device, OutputDevice 
import time
import datetime
import random 

# --- CRITICAL: Set the Device to use the Mock Pin Factory ---
Device.pin_factory = MockFactory()

# ----------------------------------------------------------------------
# 1. CONFIGURATION AND PARAMETERS (Admin-set target thresholds)
# ----------------------------------------------------------------------

# Pin Numbers (Using BCM numbering scheme - used by the Mock Factory)
PIN_ACTUATOR_PUMP = 17      # Irrigation Pump Relay
PIN_ACTUATOR_FAN = 27       # Cooling Fan/Ventilation Relay
PIN_ACTUATOR_MISTER = 22    # Humidifier/Mister Relay
PIN_ACTUATOR_LED = 23       # LED Grow Lights Relay <-- NEW PIN
PIN_ACTUATOR_SHADE = 24     # Shade Net/Curtain Relay <-- NEW PIN

# Automatic Control Thresholds
# Soil Moisture (%)
TARGET_MOISTURE_LOW = 40.0    
TARGET_MOISTURE_HIGH = 65.0   

# Temperature (°C)
TARGET_TEMP_HIGH = 32.0     
TARGET_TEMP_OPTIMAL = 28.0  

# Humidity (%)
TARGET_HUMIDITY_HIGH = 80.0 
TARGET_HUMIDITY_LOW = 60.0  

# Light Intensity (Lux) <-- NEW TARGETS
TARGET_LIGHT_HIGH = 30000.0 # Turn Shade ON/Deploy if light > this (e.g., 30,000 Lux)
TARGET_LIGHT_LOW = 15000.0  # Turn LEDs ON if light < this (e.g., 15,000 Lux)

# Loop delay (in seconds)
LOOP_DELAY = 5 

# ----------------------------------------------------------------------
# 2. SENSOR VALUE SIMULATION 
# ----------------------------------------------------------------------

global simulated_moisture, simulated_temp, simulated_humidity, simulated_light
simulated_moisture = 75.0 
simulated_temp = 28.0 
simulated_humidity = 70.0 
simulated_light = 20000.0 # Starting light intensity

def get_soil_moisture_reading():
    global simulated_moisture
    if simulated_moisture > 70:
        simulated_moisture -= random.uniform(1.0, 3.0)
    elif simulated_moisture < 30:
        simulated_moisture += random.uniform(5.0, 10.0)
    else:
        simulated_moisture += random.uniform(-1.0, 1.0)
    simulated_moisture = max(0.0, min(100.0, simulated_moisture))
    return simulated_moisture

def get_temperature_reading(): 
    global simulated_temp
    simulated_temp += random.uniform(-0.1, 0.5) 
    simulated_temp = max(15.0, min(40.0, simulated_temp))
    return round(simulated_temp, 1)

def get_humidity_reading(): 
    global simulated_humidity
    simulated_humidity += random.uniform(-1.0, 0.5) 
    simulated_humidity = max(30.0, min(95.0, simulated_humidity))
    return round(simulated_humidity, 1)

def get_light_reading(): # <-- NEW LIGHT SIMULATION
    """Simulates reading the light intensity (Lux)."""
    global simulated_light
    # Simulate fluctuation, allowing for both very bright and very dark conditions
    simulated_light += random.uniform(-1000.0, 2500.0) 
    simulated_light = max(0.0, min(60000.0, simulated_light))
    return round(simulated_light, 0)

# ----------------------------------------------------------------------
# 3. LOCAL DATA BUFFER LOGGING
# ----------------------------------------------------------------------

def log_action(sensor_data, action):
    """Logs the action and sensor data locally."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {sensor_data}. Action: {action}"
    
    print(log_entry) 
    
    try:
        with open("ActuatorLog_Local.txt", "a") as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"Error writing to log file: {e}")

# ----------------------------------------------------------------------
# 4. MAIN EDGE CONTROL LOOP 
# ----------------------------------------------------------------------

def main_control_loop():
    """Contains the autonomous control logic running on the Edge Device (Mocked)."""
    
    # Setup devices using OutputDevice (simulating the relays)
    pump = OutputDevice(PIN_ACTUATOR_PUMP) 
    fan = OutputDevice(PIN_ACTUATOR_FAN) 
    mister = OutputDevice(PIN_ACTUATOR_MISTER) 
    led_light = OutputDevice(PIN_ACTUATOR_LED)     # <-- NEW DEVICE
    shade_net = OutputDevice(PIN_ACTUATOR_SHADE)   # <-- NEW DEVICE
    
    # Set all actuators to OFF initially
    pump.off() 
    fan.off() 
    mister.off() 
    led_light.off() # <-- INITIAL STATE
    shade_net.off() # <-- INITIAL STATE
    
    # Initial state logging (Logging all four parameters)
    log_action(f"Moisture: {get_soil_moisture_reading():.1f}%", "System Initialized: Pump OFF")
    log_action(f"Temp: {get_temperature_reading():.1f}°C", "System Initialized: Fan OFF")
    log_action(f"Humidity: {get_humidity_reading():.1f}%", "System Initialized: Mister OFF")
    log_action(f"Light: {get_light_reading():.0f} Lux", "System Initialized: Light OFF") # <-- NEW LOG

    print("\n--- Edge Control System Running (4-Axis Control, MOCK MODE) ---\n"),
    
    try:
        while True:
            # Read All Sensors
            current_moisture = get_soil_moisture_reading()
            current_temp = get_temperature_reading()
            current_humidity = get_humidity_reading()
            current_light = get_light_reading() # <-- NEW SENSOR READ

            # --- A. SOIL MOISTURE CONTROL (Irrigation Pump) ---
            if current_moisture < TARGET_MOISTURE_LOW and pump.is_active == False:
                pump.on() 
                log_action(f"Soil_Moisture: {current_moisture:.1f}%", "Pump ON (Irrigation START)")
            elif current_moisture > TARGET_MOISTURE_HIGH and pump.is_active == True:
                pump.off() 
                log_action(f"Soil_Moisture: {current_moisture:.1f}%", "Pump OFF (Irrigation STOP)")
            else:
                log_action(f"Soil_Moisture: {current_moisture:.1f}%", f"Soil_Moisture: No Change ({'ON' if pump.is_active else 'OFF'})")


            # --- B. TEMPERATURE CONTROL (Cooling Fan) ---
            if current_temp > TARGET_TEMP_HIGH and fan.is_active == False:
                fan.on() 
                log_action(f"Temperature: {current_temp:.1f}°C", "Fan ON (Cooling START)")
            elif current_temp < TARGET_TEMP_OPTIMAL and fan.is_active == True:
                fan.off() 
                log_action(f"Temperature: {current_temp:.1f}°C", "Fan OFF (Cooling STOP)")
            else:
                log_action(f"Temperature: {current_temp:.1f}°C", f"Temperature: No Change ({'ON' if fan.is_active else 'OFF'})")


            # --- C. HUMIDITY CONTROL (Mister & Fan) ---
            # 1. Humidify (Mister ON)
            if current_humidity < TARGET_HUMIDITY_LOW and mister.is_active == False:
                mister.on() 
                log_action(f"Humidity: {current_humidity:.1f}%", "Mister ON (Humidify START)")
                
            # 2. Stop Humidifying (Mister OFF)
            elif current_humidity > TARGET_HUMIDITY_HIGH and mister.is_active == True:
                mister.off() 
                log_action(f"Humidity: {current_humidity:.1f}%", "Mister OFF (Humidify STOP)")
            
            # 3. Dehumidify (Fan ON - using the same fan as temp control)
            elif current_humidity > TARGET_HUMIDITY_HIGH and fan.is_active == False:
                fan.on()
                log_action(f"Humidity: {current_humidity:.1f}%", "Fan ON (Dehumidify START)")
            
            else:
                log_action(f"Humidity: {current_humidity:.1f}%", f"Humidity: No Change ({'ON' if mister.is_active else 'OFF'})")


            # --- D. LIGHT INTENSITY CONTROL (LEDs & Shade Net) --- <-- NEW CONTROL LOGIC
            
            # 1. Too Dark - Turn LEDs ON
            if current_light < TARGET_LIGHT_LOW and led_light.is_active == False:
                led_light.on()
                log_action(f"Light_Intensity: {current_light:.0f} Lux", "LEDs ON (Low Light Supplement)")
                
            # 2. Optimal Light - Turn LEDs OFF
            elif current_light > (TARGET_LIGHT_LOW + 500) and led_light.is_active == True:
                # Adding a small buffer (500 Lux) to prevent rapid ON/OFF cycling
                led_light.off()
                log_action(f"Light_Intensity: {current_light:.0f} Lux", "LEDs OFF (Light Optimal)")

            # 3. Too Bright - Deploy Shade Net
            elif current_light > TARGET_LIGHT_HIGH and shade_net.is_active == False:
                shade_net.on()
                log_action(f"Light_Intensity: {current_light:.0f} Lux", "Shade Net ON (High Light Protection)")
                
            # 4. Light Reduced - Retract Shade Net
            elif current_light < (TARGET_LIGHT_HIGH - 1000) and shade_net.is_active == True:
                # Adding a larger buffer (1000 Lux) for shade retraction stability
                shade_net.off()
                log_action(f"Light: {current_light:.0f} Lux", "Shade Net OFF (Light Reduced)")

            else:
                light_state = f"LED:{'ON' if led_light.is_active else 'OFF'}, Shade:{'ON' if shade_net.is_active else 'OFF'}"
                log_action(f"Light_Intensity: {current_light:.0f} Lux", f"Light: No Change ({light_state})")


            # Wait for the next cycle
            time.sleep(LOOP_DELAY) 

    except KeyboardInterrupt:
        print("\nEdge Control System Stopped by User.")
        
    except Exception as e:
        print(f"\nAn error occurred: {e}")


    finally:
        # Clean up all devices
        pump.off()
        fan.off()
        mister.off()
        led_light.off()
        shade_net.off()
        print("Mock device cleanup completed. Program exit.")

# Start the program
if __name__ == "__main__":
    main_control_loop()
    