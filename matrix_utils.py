import requests
import uuid
import json
import os
from env_config import MATRIX_HOMESERVER, MATRIX_ROOM_ID, MATRIX_USER, MATRIX_PASS

MATRIX_TOKEN_FILE = "matrix_access_token.txt"

def perform_matrix_login():
    if not MATRIX_USER or not MATRIX_PASS:
        return None
    
    login_url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/login"
    payload = {
        "type": "m.login.password",
        "user": MATRIX_USER,
        "password": MATRIX_PASS,
        "initial_device_display_name": "KiteScannerAuto"
    }
    
    try:
        response = requests.post(login_url, json=payload, timeout=15)
        if response.status_code == 200:
            token = response.json().get("access_token")
            if token:
                with open(MATRIX_TOKEN_FILE, "w") as f:
                    f.write(token)
                print("Matrix auto-login successful.")
                return token
        else:
            print(f"Matrix auto-login failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Matrix auto-login error: {e}")
    return None

def get_matrix_token():
    # 1. Try to read from file first
    token = None
    if os.path.exists(MATRIX_TOKEN_FILE):
        try:
            with open(MATRIX_TOKEN_FILE, "r") as f:
                token = f.read().strip()
        except Exception as e:
            print(f"Error reading {MATRIX_TOKEN_FILE}: {e}")
    
    # 2. Fallback to environment variable
    if not token:
        token = os.getenv("MATRIX_ACCESS_TOKEN")
        
    # 3. Auto-login if still no token
    if not token:
        token = perform_matrix_login()
        
    return token

def send_matrix_message(message, room_id=None):
    token = get_matrix_token()
    if not token:
        return None

    target_room = room_id if room_id else MATRIX_ROOM_ID
    if not target_room:
        print("Matrix Room ID missing!")
        return None

    txn_id = str(uuid.uuid4())
    url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_id}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "msgtype": "m.text",
        "body": message
    }
    
    try:
        response = requests.put(url, headers=headers, data=json.dumps(payload), timeout=10)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 401:
            print("Matrix token expired/invalid. Attempting auto-login...")
            new_token = perform_matrix_login()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                response = requests.put(url, headers=headers, data=json.dumps(payload), timeout=10)
                if response.status_code == 200:
                    return response.json()
            print(f"Failed to send Matrix message after retry: {response.status_code} - {response.text}")
            return None
        else:
            print(f"Failed to send Matrix message: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error sending Matrix message: {e}")
        return None
