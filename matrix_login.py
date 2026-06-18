import requests
import sys
import os

def get_permanent_token(homeserver, username, password, device_name="KiteScannerBot"):
    """
    Generates a long-lived access token by logging in with a specific device name.
    As long as you don't 'logout' this device, the token remains valid.
    """
    if not homeserver.startswith("http"):
        homeserver = f"https://{homeserver}"
    
    login_url = f"{homeserver}/_matrix/client/v3/login"
    
    payload = {
        "type": "m.login.password",
        "user": username,
        "password": password,
        "initial_device_display_name": device_name
    }
    
    print(f"Attempting to login to {homeserver} for user {username}...")
    
    try:
        response = requests.post(login_url, json=payload, timeout=15)
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")
            device_id = data.get("device_id")
            print("\n" + "="*50)
            print("SUCCESS! PERMANENT TOKEN GENERATED")
            print("="*50)
            print(f"Access Token: {token}")
            print(f"Device ID:    {device_id}")
            print("="*50)
            print("\nINSTRUCTIONS:")
            print("1. Copy the Access Token above.")
            print("2. Set it as MATRIX_ACCESS_TOKEN in your Railway environment variables.")
            print("3. OR save it to 'C:\\Users\\kalpe\\zarodha\\matrix_access_token.txt'")
            print("\nNOTE: This token will remain valid forever unless you manually")
            print("log out this specific device ID from your Matrix account settings.")
            return token
        else:
            print(f"Login failed! Status: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")
    return None

if __name__ == "__main__":
    print("Matrix Permanent Token Generator")
    hs = input("Homeserver (e.g., https://matrix.org): ").strip() or "https://matrix.org"
    user = input("Username (e.g., @yourbot:matrix.org): ").strip()
    pw = input("Password: ").strip()
    
    if not user or not pw:
        print("Username and Password are required.")
    else:
        get_permanent_token(hs, user, pw)
