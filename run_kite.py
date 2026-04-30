import threading
import os
import time
import schedule
import requests
from flask import Flask, request
from datetime import datetime
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect
from env_config import API_KEY, API_SECRET
from scanner import run_scanner
from websocket_flow import FlowEngine
from telegram_utils import send_telegram_message

# --- Configuration ---
IST = ZoneInfo("Asia/Kolkata")
TOKEN_FILE = "access_token.txt"
AUTO_START_SCANNER = os.getenv("AUTO_START_SCANNER", "true").lower() in ("true", "1", "yes")
AUTO_START_BACKGROUND = os.getenv("AUTO_START_BACKGROUND", "true").lower() in ("true", "1", "yes")

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

# Global handles
scanner_thread = None
flow_engine = None
scanner_lock = threading.Lock()
background_started = False

# --- Utility Functions ---

def mask_value(value, keep=4):
    if not value or value == "YOUR_API_KEY": return "missing"
    return f"{value[:keep]}..."

def load_saved_token():
    if not os.path.exists(TOKEN_FILE): return None
    with open(TOKEN_FILE, "r") as f:
        return f.read().strip()

def validate_and_start_scanner(source):
    global scanner_thread, flow_engine
    with scanner_lock:
        if scanner_thread and scanner_thread.is_alive():
            print(f"[{source}] Scanner already running.")
            return True

        token = load_saved_token()
        if not token:
            print(f"[{source}] No access token found. Cannot start scanner.")
            return False

        try:
            kite.set_access_token(token)
            kite.profile() # Validation call
            print(f"[{source}] Token validated. Starting Engine...")
            
            flow_engine = FlowEngine(kite)
            flow_engine.start()

            scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
            scanner_thread.daemon = True
            scanner_thread.start()
            return True
        except Exception as e:
            print(f"[{source}] Validation failed: {e}")
            return False

# --- Scheduler Tasks ---

def update_instruments():
    print("Updating instruments.csv...")
    try:
        r = requests.get("https://api.kite.trade/instruments", timeout=30)
        if r.status_code == 200:
            with open("instruments.csv", "wb") as f:
                f.write(r.content)
            print("Instruments updated.")
            # Make sure this URL is correct for your Railway deployment
            send_telegram_message("✅ Instruments Updated. Please log in to start scanner: https://your-app-url.up.railway.app/login")
    except Exception as e:
        print(f"Update Error: {e}")

def morning_task():
    now = datetime.now(IST)
    if now.weekday() > 4: return # Skip weekends
    
    print(f"Morning Task Started at {now.strftime('%H:%M')}")
    update_instruments()

def run_scheduler_loop():
    print("Background Scheduler Active.")
    schedule.every().monday.at("08:30").do(morning_task)
    schedule.every().tuesday.at("08:30").do(morning_task)
    schedule.every().wednesday.at("08:30").do(morning_task)
    schedule.every().thursday.at("08:30").do(morning_task)
    schedule.every().friday.at("08:30").do(morning_task)
    while True:
        schedule.run_pending()
        time.sleep(10)

# --- Gunicorn Hooks / App Initialization ---
def start_background_services(source):
    global background_started
    with scanner_lock:
        if background_started:
            print(f"[{source}] Background services already started.")
            return
        background_started = True

    print(f"[{source}] Starting background tasks...")
    sched_thread = threading.Thread(target=run_scheduler_loop, daemon=True)
    sched_thread.start()

    if AUTO_START_SCANNER and load_saved_token():
        boot_thread = threading.Thread(
            target=validate_and_start_scanner,
            args=(source,),
            daemon=True
        )
        boot_thread.start()
    elif AUTO_START_SCANNER:
        print(f"[{source}] Scanner auto-start skipped: login required.")


def ensure_background_services_started(source):
    if AUTO_START_BACKGROUND:
        start_background_services(source)

# --- Flask Routes ---

@app.route("/")
def home():
    ensure_background_services_started("HTTP /")
    # This route is now purely for health checks and basic status
    status = "RUNNING" if (scanner_thread and scanner_thread.is_alive()) else "STOPPED"
    return f"<h3>Kite Scanner Status: {status}</h3><p>Server Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}</p>"

@app.route("/login")
def login():
    ensure_background_services_started("HTTP /login")
    request_token = request.args.get("request_token")
    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Action Required</h3><p><a href='{login_url}'>Click here to login to Zerodha</a></p>"

    try:
        data = kite.generate_session(request_token, API_SECRET)
        token = data["access_token"]
        with open(TOKEN_FILE, "w") as f:
            f.write(token)
        validate_and_start_scanner("Manual Login")
        return "<h1>Success!</h1><p>Login successful and scanner started.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>{str(e)}</p>"

# This block is only executed when run directly (e.g., `python run_kite.py`)
if __name__ == "__main__":
    print(f"Starting Flask Dev Server directly on port {os.getenv('PORT', 8080)}...")
    # For local testing, we still want to start background tasks
    start_background_services("Direct Run")
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
