from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
from websocket_flow import FlowEngine
import threading
import os
from datetime import datetime

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)
scanner_thread = None
flow_engine = None
scanner_lock = threading.Lock()
AUTO_START_ON_IMPORT = os.getenv("AUTO_START_SCANNER_ON_IMPORT", "").strip().lower() in {"1", "true", "yes"}
TOKEN_FILE = "access_token.txt"


def mask_value(value, keep=4):
    if not value:
        return "missing"
    if len(value) <= keep:
        return value
    return f"{value[:keep]}..."


def load_saved_token():
    if not os.path.exists(TOKEN_FILE):
        print(f"Token file not found: {TOKEN_FILE}")
        return None

    with open(TOKEN_FILE, "r") as f:
        token = f.read().strip()

    if not token:
        print(f"Token file is empty: {TOKEN_FILE}")
        return None

    try:
        mtime = os.path.getmtime(TOKEN_FILE)
        modified = datetime.fromtimestamp(mtime).isoformat(sep=" ", timespec="seconds")
        print(f"Loaded access token from {TOKEN_FILE} (modified {modified}).")
    except OSError:
        print(f"Loaded access token from {TOKEN_FILE}.")

    return token


def validate_access_token(access_token, source_label):
    try:
        kite.set_access_token(access_token)
        profile = kite.profile()
        user_id = profile.get("user_id") or profile.get("user_shortname") or "unknown"
        print(
            f"Validated Zerodha session from {source_label}. "
            f"api_key={mask_value(API_KEY)} user={user_id}"
        )
        return True
    except Exception as e:
        print(
            f"Rejected Zerodha session from {source_label}. "
            f"api_key={mask_value(API_KEY)} error={e}"
        )
        return False

def start_scanner_if_token_exists():
    global scanner_thread, flow_engine
    with scanner_lock:
        if scanner_thread is not None and scanner_thread.is_alive():
            print("Scanner already running. Skipping duplicate start.")
            return True

        try:
            token = load_saved_token()
            if not token:
                return False

            if not validate_access_token(token, "saved token file"):
                print("Scanner not started because the saved access token is invalid or expired.")
                return False

            print("Starting scanner with validated saved token...")
            flow_engine = FlowEngine(kite)
            flow_engine.start()

            scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
            scanner_thread.daemon = True
            scanner_thread.start()
            return True
        except Exception as e:
            print(f"Failed to auto-start scanner: {e}")
    return False

@app.route("/")
def home():
    return "Server is Live. Scanner is running."

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Action Required</h3><p><a href='{login_url}'>Log into Zerodha Kite</a></p>"

    try:
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        if not validate_access_token(access_token, "login callback"):
            return "<h1>Error</h1><p>Login succeeded, but the access token was rejected during validation.</p>"
        with open(TOKEN_FILE, "w") as f:
            f.write(access_token)
        start_scanner_if_token_exists()
        return "<h1>Success!</h1><p>Scanner is now running.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>Login failed: {str(e)}</p>"

if AUTO_START_ON_IMPORT:
    start_scanner_if_token_exists()

if __name__ == "__main__":
    start_scanner_if_token_exists()
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
