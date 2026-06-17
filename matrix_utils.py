import requests
import uuid
import json
from env_config import MATRIX_HOMESERVER, MATRIX_ACCESS_TOKEN, MATRIX_ROOM_ID

def send_matrix_message(message, room_id=None):
    if not MATRIX_ACCESS_TOKEN:
        # Silently skip if not configured
        return None

    target_room = room_id if room_id else MATRIX_ROOM_ID
    if not target_room:
        print("Matrix Room ID missing!")
        return None

    txn_id = str(uuid.uuid4())
    url = f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{target_room}/send/m.room.message/{txn_id}"
    
    headers = {
        "Authorization": f"Bearer {MATRIX_ACCESS_TOKEN}",
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
