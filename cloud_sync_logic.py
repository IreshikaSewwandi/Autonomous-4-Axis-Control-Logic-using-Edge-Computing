import csv
import os
import random

# --- CONFIGURATION ---
LOCAL_LOG_FILE = "ActuatorLog_Local.txt"
CLOUD_READY_CSV = "CloudSync_Ready.csv"
# Define the threshold for what constitutes a 'critical' action
CRITICAL_ACTIONS = ["Pump ON", "Fan ON", "Mister ON", "Shade Net ON", "LEDs ON"] 

def parse_log_entry(line):
    """
    Parses a single log line into structured data fields.
    Example line format: [2025-12-05 10:55:01] Moisture: 68.1%. Action: Moisture: No Change (OFF)
    """
    try:
        # Extract Timestamp
        timestamp = line[1:20] # [YYYY-MM-DD HH:MM:SS]
        
        # Split sensor data and action
        data_parts = line[23:].split(". Action: ")
        
        # Extract Sensor Type and Value
        # Data part example: 'Moisture: 68.1%' or 'Temp: 32.5°C'
        sensor_data_raw = data_parts[0].split(': ')
        sensor_type = sensor_data_raw[0]
        sensor_value_unit = sensor_data_raw[1].strip()
        
        # Extract Action and State
        action_raw = data_parts[1].strip()
        action_name = action_raw.split('(')[0].strip()
        
        # Determine if the action is critical
        is_critical = any(crit_action in action_name for crit_action in CRITICAL_ACTIONS)

        # Structure the parsed data
        return {
            "Timestamp": timestamp,
            "Sensor_Type": sensor_type,
            "Sensor_Reading": sensor_value_unit,
            "Actuator_Action": action_name,
            "Is_Critical": "Yes" if is_critical else "No",
            # Add a mock Cloud Sync ID for the final step
            "Cloud_Sync_ID": f"CS-{random.randint(1000, 9999)}" 
        }
    except Exception as e:
        # Handle lines that don't match the expected format
        return None

def read_local_buffer_and_sync():
    """
    Reads data from the local text file, processes it, and simulates synchronization.
    """
    if not os.path.exists(LOCAL_LOG_FILE):
        print(f"ERROR: Local log file not found at {LOCAL_LOG_FILE}")
        return

    processed_data = []
    
    # --- STEP 1: READ and PARSE the Local Buffer ---
    print(f"Reading data from {LOCAL_LOG_FILE}...")
    with open(LOCAL_LOG_FILE, 'r') as f:
        for line in f:
            structured_entry = parse_log_entry(line.strip())
            if structured_entry:
                processed_data.append(structured_entry)
            
    print(f"Successfully parsed {len(processed_data)} entries.")

    if not processed_data:
        print("No new data to synchronize.")
        return

    # --- STEP 2: SIMULATE Sending Data to Cloud Endpoint ---
    # In a real system, this is where you would use an HTTP POST request or MQTT PUBLISH
    # to send the 'processed_data' list to your AWS or Azure backend.
    
    successful_sync_count = len(processed_data)
    print(f"\n--- Cloud Synchronization Simulation ---")
    print(f"Attempting to send {successful_sync_count} records to Cloud Endpoint...")
    print(f"SUCCESS: {successful_sync_count} records received by mock cloud service.")

    # --- STEP 3: EXPORT Processed Data to CSV (Export to Sheets) ---
    fieldnames = processed_data[0].keys()
    
    with open(CLOUD_READY_CSV, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_data)
        
    print(f"\nSUCCESS: Data exported to {CLOUD_READY_CSV}")
    print("This CSV file can now be easily imported into Google Sheets or Excel.")
    
    # --- STEP 4: CLEANUP (Clear Local Buffer after successful sync) ---
    # In a real Edge system, you would clear the local buffer (e.g., delete the text file 
    # or clear the SQLite table) ONLY after verifying the cloud received the data.
    
    # os.remove(LOCAL_LOG_FILE) # Uncomment this line in the final product!
    print(f"\nCleanup: {LOCAL_LOG_FILE} ready for next cycle (file was NOT deleted in this demo).")

if __name__ == "__main__":
    read_local_buffer_and_sync()