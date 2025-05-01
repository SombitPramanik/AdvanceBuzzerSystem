# main.py or boot.py on your ESP32

import machine
import network
import time
import urequests
import ujson
#import socket # Import socket to get the IP address

# --- Configuration ---
WIFI_SSID = "Widfi_SSID"
WIFI_PASSWORD = "Wifi_Password"
SERVER_IP = "ServerHostName" # like raspberripy.local,192.168.0.10
SERVER_PORT = 5000 # Default Flask port
SERVER_URL = f"http://{SERVER_IP}:{SERVER_PORT}"

CLIENT_NAME = "DeviceB"
# Client IP will be obtained dynamically after connecting to WiFi

# Pin definitions
ONBOARD_LED_PIN = 2  # GPIO 2 is often the onboard LED on ESP32 DevKits
TARGET_LED_PIN = 25  # GPIO 25 for the target indicator LED
SWITCH_PIN = 34      # GPIO 34 for the switch input (ADC capable)

# ADC Configuration for Pin 34
# ESP32 ADC reference voltage is typically 1100mV (1.1V) by default.
# With attenuation, it can read higher voltages.
# ADC.ATTN_11DB allows reading up to 3.3V (approx).
# Max ADC value is 4095 for 12-bit resolution.
# We want a threshold around 3.1V.
# 3.1V / 3.3V * 4095 ≈ 3840
ADC_PIN34_THRESHOLD = 2940 # ADC reading threshold for HIGH (approx 3.1V)

# Communication Intervals
# Regular status check interval
STATUS_CHECK_INTERVAL_SECONDS = 10
# Duration Pin 34 must be continuously HIGH to trigger immediate communication
PIN34_HIGH_DURATION_THRESHOLD = 3

RECONNECT_DELAY_SECONDS = 5 # Delay before retrying WiFi or server connection

# --- Pin Setup ---
onboard_led = machine.Pin(ONBOARD_LED_PIN, machine.Pin.OUT)
target_led = machine.Pin(TARGET_LED_PIN, machine.Pin.OUT)
# Configure switch pin as input with pull-down resistor
# If your switch pulls UP to 3.3V when pressed, use PULL_DOWN
# If your switch pulls DOWN to GND when pressed (and pin is pulled up), use PULL_UP
# Assuming switch connects to 3.3V when pressed and pin needs pull-down
switch_pin = machine.Pin(SWITCH_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)

# Configure ADC on the switch pin
adc_pin34 = machine.ADC(machine.Pin(SWITCH_PIN))
adc_pin34.atten(machine.ADC.ATTN_11DB) # Set attenuation to read up to 3.3V


# --- WiFi Connection ---
def connect_wifi(ssid, password):
    """Connects the ESP32 to the specified WiFi network."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    # Set hostname (MicroPython might not fully support arbitrary hostnames this way,
    # but it's the standard method. Verification might be needed.)
    wlan.config(hostname=CLIENT_NAME)
    print(f"Attempting to connect to WiFi SSID: {ssid}")
    wlan.connect(ssid, password)

    # Wait for connection with timeout
    max_wait = 30 # Increased wait time for potentially slower networks
    while max_wait > 0:
        if wlan.isconnected():
            print("WiFi connected!")
            onboard_led.value(1) # Turn on onboard LED
            client_ip = wlan.ifconfig()[0] # Get the assigned IP address
            print(f"Device IP address: {client_ip}")
            return wlan, client_ip
        time.sleep(0.5) # Check more frequently during connection attempt
        max_wait -= 1

    print("WiFi connection failed.")
    onboard_led.value(0) # Ensure LED is off if connection fails
    wlan.active(False) # Deactivate WLAN if connection fails
    return None, None

# --- Read Switch State using ADC ---
def read_switch_adc():
    """Reads the ADC value of the switch pin and determines High/Low based on threshold."""
    adc_value = adc_pin34.read()
    # print(f"ADC value: {adc_value}") # Uncomment for debugging ADC values
    if adc_value >= ADC_PIN34_THRESHOLD:
        return "High"
    else:
        return "Low"

# --- Server Communication Functions ---
def send_connect_request(client_ip, status, pin34_status, engaged):
    """Sends client data to the /Connect endpoint."""
    connect_url = f"{SERVER_URL}/Connect"
    client_data = {
        "name": CLIENT_NAME,
        "ip": client_ip,
        "status": status,
        "pin34_status": pin34_status,
        "engaged": engaged
    }
    try:
        # Use urequests for HTTP POST
        # print(f"Sending Connect request: {client_data}") # Commented out print
        response = urequests.post(connect_url, json=client_data)
        response_json = response.json()
        # print(f"Connect response: {response_json}") # Commented out print
        response.close() # Close the response to free up resources
        return response_json
    except Exception as e:
        print(f"Error sending Connect request: {e}") # Keep error prints
        return None

def get_status_request(client_ip):
    """Gets system status from the /Status endpoint."""
    # Include name and ip as query parameters for activity tracking
    status_url = f"{SERVER_URL}/Status?name={CLIENT_NAME}&ip={client_ip}"
    try:
        # Use urequests for HTTP GET
        # print(f"Sending Status request to {status_url}") # Commented out print
        response = urequests.get(status_url)
        response_json = response.json()
        # print(f"Status response: {response_json}") # Commented out print
        response.close() # Close the response to free up resources
        return response_json
    except Exception as e:
        print(f"Error getting Status: {e}") # Keep error prints
        return None

# --- Main Application Logic ---
def main():
    """Main function to connect, communicate, and manage LEDs."""
    wlan, client_ip = connect_wifi(WIFI_SSID, WIFI_PASSWORD)

    if not wlan or not client_ip:
        print("Failed to start. Check WiFi connection.")
        # Optionally add a delay and retry WiFi connection here
        # time.sleep(RECONNECT_DELAY_SECONDS)
        # machine.reset() # Or attempt reconnect logic
        return # Exit if WiFi connection failed

    last_communication_time = time.time() # Track the last time we communicated with the server
    last_pin34_state = read_switch_adc()
    pin34_high_start_time = None # Timestamp when Pin 34 first went HIGH

    # Send initial state to the server
    send_connect_request(client_ip, "Connected", last_pin34_state, "No")
    # Get initial status after sending initial state
    get_status_request(client_ip)


    while True:
        current_time = time.time()

        # --- Read Current Switch State ---
        current_pin34_state = read_switch_adc()

        # --- Pin 34 High Duration Tracking ---
        if current_pin34_state == "High" and last_pin34_state == "Low":
            # Pin just went High, record the start time
            pin34_high_start_time = current_time
            # print(f"Pin 34 detected HIGH at {pin34_high_start_time}") # Commented out print
        elif current_pin34_state == "Low" and last_pin34_state == "High":
            # Pin just went Low, reset the start time
            pin34_high_start_time = None
            # print("Pin 34 detected LOW, resetting high duration timer.") # Commented out print

        # Update the last known state after reading
        last_pin34_state = current_pin34_state

        # --- Determine if Communication Cycle is Needed ---
        perform_communication_cycle = False

        # 1. Regular interval check (10 seconds)
        if current_time - last_communication_time >= STATUS_CHECK_INTERVAL_SECONDS:
            # print(f"Regular communication interval ({STATUS_CHECK_INTERVAL_SECONDS}s) reached.") # Commented out print
            perform_communication_cycle = True

        # 2. Override check: Pin 34 continuously High for threshold duration
        if current_pin34_state == "High" and pin34_high_start_time is not None:
            elapsed_high_time = current_time - pin34_high_start_time
            if elapsed_high_time >= PIN34_HIGH_DURATION_THRESHOLD:
                # print(f"Pin 34 continuously HIGH for {elapsed_high_time:.2f}s (>= {PIN34_HIGH_DURATION_THRESHOLD}s). Triggering immediate communication.") # Commented out print
                perform_communication_cycle = True
                # Reset the high start time AFTER triggering the communication
                # This prevents triggering multiple times for the same continuous high event
                pin34_high_start_time = None # Reset after triggering


        # --- Perform Communication Cycle if Needed ---
        if perform_communication_cycle:
            # print("\nPerforming communication cycle...") # Commented out print
            # Always send a Connect request with the current state before getting status
            send_connect_request(client_ip, "Connected", current_pin34_state, "No")

            status_info = get_status_request(client_ip)

            if status_info:
                server_status = status_info.get("status")
                current_target = status_info.get("target")

                # --- Target LED Control ---
                if server_status == "Already in target" and current_target == CLIENT_NAME:
                    # print(f"Device '{CLIENT_NAME}' is the current target. Turning on target LED.") # Commented out print
                    target_led.value(1) # Turn on target LED
                else:
                    # If waiting, or target is someone else, turn off target LED
                    # print(f"Device '{CLIENT_NAME}' is NOT the current target. Turning off target LED.") # Commented out print
                    target_led.value(0) # Turn off target LED

            last_communication_time = current_time # Update last communication time


        # --- Keep Alive / Yield ---
        # Sleep for a short duration. The loop continuously monitors the pin
        # and checks the conditions for communication.
        time.sleep(0.05) # Reduced sleep further for more frequent pin checks


    # This part is technically unreachable in the infinite loop, but good practice
    wlan.disconnect()
    wlan.active(False)
    onboard_led.value(0)
    target_led.value(0)
    print("Disconnected and stopped.")


# --- Start the main function ---
if __name__ == "__main__":
    main()

