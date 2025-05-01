import random
import time
import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import threading
import json

# Initialize Flask app and SocketIO
app = Flask(__name__)
# Set a secret key for Flask sessions (required by Flask-SocketIO)
# *** IMPORTANT: Replace with a real, complex secret key in production ***
app.config["SECRET_KEY"] = "secretkey"
Socket = SocketIO(app)

# --- Backend State Variables ---
SystemStatus = "OK"
# Define error codes for different scenarios
ErrorCodes = {
    "All Device are not Connected": "99",
    "All device are Connected": "69", # This might be a success code, adjust as needed
    "No Clients Registered": "101" # Added a code for when ClientsData is empty
}
CurrentError = None # Will hold the error code if SystemStatus is ERROR
CurrentTarget = "Initializing.." # Name of the current target device/operation
ClientsData = [] # List to hold data for connected client devices

# Variables for Target Reset Logic
TargetResetTime = None # Timestamp when the current target was set
ResetTimerDuration = 20 # Default duration for target reset in seconds

# Client Inactivity Timeout
CLIENT_TIMEOUT_SECONDS = 20 # Time in seconds after which an inactive client is removed

# Flag to ensure background task runs only once
BackgroundTask = False

# --- Helper function to find client by name or IP and update activity ---
def find_client_and_update_activity(name=None, ip=None):
    """
    Finds a client in ClientsData by name or IP and updates their last_activity timestamp.
    Returns the client dictionary if found, otherwise None.
    """
    found_client = None
    for client in ClientsData:
        if (name and client.get('name') == name) or (ip and client.get('ip') == ip):
            client['last_activity'] = time.time() # Update timestamp
            found_client = client
            break
    return found_client

# --- Background Task to Update State and Emit to Frontend ---
def BackgroundUiUpdater():
    """
    Periodically checks the backend state, manages target selection/reset,
    removes inactive clients, and emits updates to the frontend.
    Also updates SystemStatus and CurrentError.
    """
    print("Background task started: Updating UI data...")
    while True:
        # --- Remove Inactive Clients ---
        global ClientsData
        current_time = time.time()
        # Create a new list containing only active clients
        active_clients = [
            client for client in ClientsData
            if current_time - client.get('last_activity', 0) <= CLIENT_TIMEOUT_SECONDS
        ]

        # Check if the current target is in the active clients list, reset if not
        global CurrentTarget, TargetResetTime
        if CurrentTarget not in ["Initializing..", None]:
            target_still_active = any(client.get('name') == CurrentTarget for client in active_clients)
            if not target_still_active:
                print(f"Current target '{CurrentTarget}' removed due to inactivity. Resetting target.")
                CurrentTarget = None
                TargetResetTime = None
                # Reset engaged status for all clients
                for client in active_clients: # Only process active clients
                    client['engaged'] = 'No'


        # Update the main ClientsData list
        if len(ClientsData) != len(active_clients):
             print(f"Removed {len(ClientsData) - len(active_clients)} inactive clients.")
             ClientsData = active_clients


        # --- Update System Status and Error based on ClientsData ---
        global SystemStatus, CurrentError
        if not ClientsData:
            SystemStatus = "ERROR"
            CurrentError = ErrorCodes["No Clients Registered"]
        else:
             SystemStatus = "OK"
             CurrentError = None

        # --- Target Selection Logic (First Come, First Served based on pin34_status == 'High') ---
        # This logic only runs if no target is currently set
        if CurrentTarget in ["Initializing..", None]:
            # No target is currently set, look for a new one among active clients
            for client in ClientsData:
                # Check if the client is connected and has pin34_status High
                # Assuming 'Connected' is the status indicating readiness to be a target
                if client.get('status') == 'Connected' and client.get('pin34_status') == 'High':
                    print(f"Client '{client.get('name')}' detected with pin34_status HIGH. Setting as target.")
                    CurrentTarget = client.get('name')
                    TargetResetTime = time.time() # Record the time the target was set
                    # Immediately set the engaged status for the selected target
                    client['engaged'] = 'Yes'
                    # Optional: Set other clients to 'No' engaged status
                    for other_client in ClientsData:
                        if other_client['name'] != CurrentTarget:
                            other_client['engaged'] = 'No'
                    break # Stop searching once a target is found

        # --- Target Reset Logic (Automatic after duration) ---
        if CurrentTarget not in ["Initializing..", None] and TargetResetTime is not None:
            elapsed_time = time.time() - TargetResetTime
            if elapsed_time >= ResetTimerDuration:
                print(f"Target '{CurrentTarget}' timer expired ({ResetTimerDuration}s). Resetting target.")
                CurrentTarget = None
                TargetResetTime = None
                # Reset engaged status for all clients when target is reset
                for client in ClientsData:
                    client['engaged'] = 'No'


        # --- Prepare and Emit Data to Frontend ---
        UI_Data = {
            "SystemStatus": SystemStatus,
            "Error": CurrentError,
            "CurrentTarget": CurrentTarget,
            "Clients": ClientsData,
            "ResetTimerDuration": ResetTimerDuration # Also send the current timer duration
        }

        # print(f"Emitting UI data: Status={SystemStatus}, Target={CurrentTarget}, Clients={len(ClientsData)}")

        # Emit the combined UI data to all connected frontend clients
        Socket.emit("UI_UPDATE", UI_Data)

        # Wait for a specified interval before the next update (e.g., 5 seconds)
        # It's good to check for inactivity more frequently than the timeout itself.
        time.sleep(5) # Check and update every 5 seconds

# --- Route for Client Devices to Connect/Register ---
# Changed to POST to properly receive JSON data in the body
@app.route("/Connect", methods=["POST"])
def ESPConnect():
    """
    Handles connection requests from client devices.
    Receives client data in JSON format, updates or adds the client in ClientsData.
    Also updates the client's last_activity timestamp.
    """
    try:
        # Get JSON data from the request body
        client_info = request.get_json()

        # Validate received data (basic check)
        if not client_info or 'name' not in client_info or 'ip' not in client_info:
            return jsonify({"status": "error", "message": "Invalid data format. 'name' and 'ip' are required."}), 400

        client_name = client_info.get('name')
        client_ip = client_info.get('ip')

        print(f"Received connection request from Name: {client_name}, IP: {client_ip}")

        # Find client and update activity timestamp
        existing_client = find_client_and_update_activity(name=client_name, ip=client_ip)

        # Default values for new/updated client
        default_client_data = {
            'id': client_info.get('id', len(ClientsData) + 1), # Use provided ID or assign a new one
            'name': client_name,
            'ip': client_ip,
            'status': client_info.get('status', 'Connecting'), # Default status
            'pin34_status': client_info.get('pin34_status', 'Unknown'), # Default PIN status
            'engaged': client_info.get('engaged', 'No'), # Default engaged status
            'last_activity': time.time() # Set initial activity timestamp
        }

        if existing_client:
            # Client exists, update their information
            print(f"Client found (Name: {client_name} or IP: {client_ip}), updating data.")
            # Update existing client data with received info, keeping existing keys if not provided
            # Use a copy to avoid modifying the list while iterating if needed, though find_client handles it
            existing_client.update({k: v for k, v in client_info.items() if v is not None})
             # Ensure all expected keys are present after update using defaults if needed
            # This merge ensures 'last_activity' from find_client_and_update_activity is kept
            merged_data = {**default_client_data, **existing_client}
            # Find the index and replace the client data
            for i, client in enumerate(ClientsData):
                 if client.get('name') == client_name or client.get('ip') == client_ip:
                      ClientsData[i] = merged_data
                      break


        else:
            # New client, add to the list
            print(f"New client connected: Name: {client_name}, IP: {client_ip}. Adding to list.")
            # Add new client data, including the last_activity timestamp
            new_client = default_client_data
            ClientsData.append(new_client)

        # Emit immediate update to frontend so it reflects the change instantly
        # This is important so the UI updates as soon as a client connects/updates
        UI_Data = {
            "SystemStatus": SystemStatus,
            "Error": CurrentError,
            "CurrentTarget": CurrentTarget,
            "Clients": ClientsData,
            "ResetTimerDuration": ResetTimerDuration
        }
        Socket.emit("UI_UPDATE", UI_Data)


        return jsonify({"status": "success", "message": "Client data received and updated."}), 200

    except Exception as e:
        print(f"Error in /Connect route: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- Route for Client Devices to Query Status ---
@app.route("/Status", methods=["GET"])
def Status():
    """
    Handles status requests from client devices.
    Returns the current target status.
    Also updates the client's last_activity timestamp based on query parameters.
    """
    # Get client identification from query parameters
    client_name = request.args.get('name')
    client_ip = request.args.get('ip')

    if client_name or client_ip:
         # Find client and update activity timestamp
         find_client_and_update_activity(name=client_name, ip=client_ip)
         # No need to emit UI update here, the background task handles periodic updates

    global CurrentTarget
    if CurrentTarget in ["Initializing..", None]:
        response_data = {"status": "Waiting to accept"}
    else:
        response_data = {"status": "Already in target", "target": CurrentTarget}

    # print(f"Status request received from Name: {client_name}, IP: {client_ip}. Responding with: {response_data}")
    return jsonify(response_data), 200

# --- Route for Manual Target Reset (Called from UI) ---
@app.route("/reset_target", methods=["POST"])
def ResetTarget():
    """
    Manually resets the current target and timer.
    """
    global CurrentTarget, TargetResetTime
    print("Manual target reset requested.")
    CurrentTarget = None
    TargetResetTime = None
    # Reset engaged status for all clients
    for client in ClientsData:
        client['engaged'] = 'No'

    # Emit immediate update to frontend
    UI_Data = {
        "SystemStatus": SystemStatus,
        "Error": CurrentError,
        "CurrentTarget": CurrentTarget,
        "Clients": ClientsData,
        "ResetTimerDuration": ResetTimerDuration
    }
    Socket.emit("UI_UPDATE", UI_Data)

    return jsonify({"status": "success", "message": "Target reset manually."}), 200

# --- Route to Update Reset Timer Duration (Called from UI) ---
@app.route("/update_reset_timer", methods=["POST"])
def UpdateResetTimer():
    """
    Updates the duration for the automatic target reset timer.
    Expects JSON body with 'duration' in seconds.
    """
    global ResetTimerDuration
    try:
        data = request.get_json()
        new_duration = data.get('duration')

        if new_duration is None or not isinstance(new_duration, (int, float)) or new_duration <= 0:
            return jsonify({"status": "error", "message": "Invalid duration provided. Must be a positive number."}), 400

        ResetTimerDuration = float(new_duration)
        print(f"Reset timer duration updated to {ResetTimerDuration} seconds.")

        # Emit immediate update to frontend so UI can show the new duration
        UI_Data = {
            "SystemStatus": SystemStatus,
            "Error": CurrentError,
            "CurrentTarget": CurrentTarget,
            "Clients": ClientsData,
            "ResetTimerDuration": ResetTimerDuration
        }
        Socket.emit("UI_UPDATE", UI_Data)


        return jsonify({"status": "success", "message": f"Reset timer duration updated to {ResetTimerDuration} seconds."}), 200

    except Exception as e:
        print(f"Error in /update_reset_timer route: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# --- Route for Frontend Index Page ---
@app.route("/")
def Index():
    """Renders the main HTML page for the frontend."""
    return render_template("index.html")

# --- Socket.IO Handlers for Frontend Clients ---
@Socket.on("connect")
def HandleConnect():
    """Handles new frontend client connections."""
    print("Front-end Client is Connected")
    # Send the current state immediately upon connection
    UI_Data = {
        "SystemStatus": SystemStatus,
        "Error": CurrentError,
        "CurrentTarget": CurrentTarget,
        "Clients": ClientsData,
        "ResetTimerDuration": ResetTimerDuration
    }
    emit("UI_UPDATE", UI_Data)


# Socket.IO handler to start the background task only once
# This handler is triggered by the first frontend client connection
@Socket.on("connect")
def StartBackgroundTask():
    """Starts the background task only once."""
    global BackgroundTask
    # Check if the background task thread is alive before starting a new one
    # This is a more robust check than just the flag
    if not BackgroundTask or not any(isinstance(t, threading.Thread) and t.name == "BackgroundUiUpdater" and t.is_alive() for t in threading.enumerate()):
        print("Starting the Background Task")
        # Assign a name to the thread for easier identification
        task_thread = threading.Thread(target=BackgroundUiUpdater, name="BackgroundUiUpdater")
        task_thread.daemon = True # Allow the main program to exit even if this thread is running
        task_thread.start()
        BackgroundTask = True
    else:
       print("Background task is already running.")


@Socket.on("disconnect")
def HandleDisconnect():
    """Handles frontend client disconnections."""
    print("Front-end Client is Disconnected from the Server")


# --- Main Execution Block ---
if __name__ == "__main__":
    # Run the Flask application with SocketIO
    # debug=True allows for auto-reloading during development
    # allow_unsafe_werkzeug=True is needed for background tasks in debug mode with some Werkzeug versions
    # Consider removing allow_unsafe_werkzeug=True in production
    # host='0.0.0.0' allows access from other machines on the network (use with caution)
    Socket.run(app=app, debug=True, allow_unsafe_werkzeug=True,host='0.0.0.0')
