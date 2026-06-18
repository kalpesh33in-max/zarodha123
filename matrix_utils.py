import requests
import uuid
import json
import os
from env_config import MATRIX_HOMESERVER, MATRIX_ROOM_ID

MATRIX_TOKEN_FILE = "matrix_access_token.txt"

def get_matrix_token():
    # 1. Try to read from file first (allows live updates without process restart)
    if os.path.exists(MATRIX_TOKEN_FILE):
        try:
            with open(MATRIX_TOKEN_FILE, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            print(f"Error reading {MATRIX_TOKEN_FILE}: {e}")
    
    # 2. Fallback to environment variable
    return os.getenv("MATRIX_ACCESS_TOKEN")

def send_matrix_message(message, room_id=None):
    token = get_matrix_token()
    if not token:
        # Silently skip if not configured
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
    
    # We use m.text. For more advanced features, we could use HTML.
    payload = {
        "msgtype": "m.text",
        "body": message
    }
    
    try:
        response = requests.put(url, headers=headers, data=json.dumps(payload), timeout=10)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Failed to send Matrix message: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error sending Matrix message: {e}")
        return None
