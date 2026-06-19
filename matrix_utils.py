import requests
import json
import os
import threading
from env_config import MATRIX_HOMESERVER, MATRIX_USER, MATRIX_PASS

# In-memory storage for the token
_matrix_token = None
_token_lock = threading.Lock()

def perform_matrix_login():
    global _matrix_token
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
                with _token_lock:
                    _matrix_token = token
                print("Matrix login successful. Token updated in memory.")
                return token
        else:
            print(f"Matrix login failed: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Matrix login error: {e}")
    return None

def get_matrix_token(force_refresh=False):
    global _matrix_token
    with _token_lock:
        if _matrix_token and not force_refresh:
            return _matrix_token
        
    # Attempt login if no token or force_refresh is True
    return perform_matrix_login()

def send_matrix_message(message, room_id=None):
    from env_config import MATRIX_ROOM_ID
    
    token = get_matrix_token()
    if not token:
        return None

    target_room = room_id if room_id else MATRIX_ROOM_ID
    if not target_room:
        print("Matrix Room ID missing!")
        return None

    import uuid
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
            print("Matrix token expired/invalid. Refreshing and retrying...")
            new_token = get_matrix_token(force_refresh=True)
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
