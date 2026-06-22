import requests
import json
import os
import threading
import time
from env_config import MATRIX_HOMESERVER, MATRIX_USER, MATRIX_PASS, MATRIX_ACCESS_TOKEN

# In-memory storage for the token. Prefer a configured token at boot, but allow
# password login to refresh it automatically when the server rejects it.
_matrix_token = MATRIX_ACCESS_TOKEN or None
_token_lock = threading.Lock()
_last_config_warning_time = 0.0
_CONFIG_WARNING_INTERVAL_SECONDS = int(os.getenv("MATRIX_CONFIG_WARNING_INTERVAL_SECONDS", "300"))

def _has_matrix_password_login():
    return bool(MATRIX_USER and MATRIX_PASS)

def perform_matrix_login(allow_static_token=False):
    global _matrix_token
    if not _has_matrix_password_login():
        _log_missing_matrix_config(allow_static_token=allow_static_token)
        return MATRIX_ACCESS_TOKEN if allow_static_token else None
    
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
        
    # Attempt login if no token or force_refresh is True.
    return perform_matrix_login(allow_static_token=not force_refresh)

def refresh_matrix_token():
    return get_matrix_token(force_refresh=True)

def _log_missing_matrix_config(allow_static_token=False):
    global _last_config_warning_time
    if allow_static_token and MATRIX_ACCESS_TOKEN:
        return

    now = time.time()
    if now - _last_config_warning_time < _CONFIG_WARNING_INTERVAL_SECONDS:
        return

    _last_config_warning_time = now
    if MATRIX_ACCESS_TOKEN:
        print(
            "Matrix password login missing: set MATRIX_USER and MATRIX_PASS. "
            "The configured MATRIX_ACCESS_TOKEN can be used only until Matrix expires it."
        )
    else:
        print("Matrix credentials missing: set MATRIX_USER/MATRIX_PASS or MATRIX_ACCESS_TOKEN.")

def send_matrix_message(message, room_id=None, is_burst=False):
    from env_config import MATRIX_ROOM_ID, MATRIX_ROOM_ID_BN, MATRIX_ROOM_ID_STOCKS
    
    token = get_matrix_token()
    if not token:
        return None

    if is_burst and MATRIX_ROOM_ID_BN:
        target_room = MATRIX_ROOM_ID_BN
    else:
        target_room = room_id if room_id else (MATRIX_ROOM_ID or MATRIX_ROOM_ID_STOCKS)
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
            if not new_token:
                print("Failed to refresh Matrix token. Check MATRIX_USER and MATRIX_PASS.")
                return None
            if new_token == token:
                print("Matrix refresh returned the same expired token. Check Matrix password login config.")
                return None
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
