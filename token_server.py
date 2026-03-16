from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
from auto_login import get_automated_token
import threading
import os
import schedule
import time
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)
IST = ZoneInfo("Asia/Kolkata")

# Global to track the scanner thread
scanner_thread = None
stop_event = threading.Event()

def start_scanner_if_token_exists():
    """Checks for access_token.txt and starts the scanner automatically."""
    global scanner_thread, stop_event
    if os.path.exists("access_token.txt"):
        try:
            with open("access_token.txt", "r") as f:
                token = f.read().strip()
            
            if token:
                print(f"Found saved token. Starting scanner automatically...")
                kite.set_access_token(token)
                
                stop_event.clear()
                scanner_thread = threading.Thread(target=run_scanner, args=(kite, stop_event))
                scanner_thread.daemon = True
                scanner_thread.start()
                return True
        except Exception as e:
            print(f"Failed to auto-start scanner: {e}")
    return False

def morning_login():
    now = datetime.now(IST)
    if now.weekday() > 4: return
    print("Starting Morning Workflow at 08:30 AM...")
    try:
        access_token = get_automated_token()
        if access_token:
            start_scanner_if_token_exists()
    except Exception as e:
        print(f"Auto Login Error: {e}")

def stop_scanner_job():
    global stop_event
    print("Stopping scanner at 15:30 PM...")
    stop_event.set()

def run_scheduler():
    """Background thread to run the scheduler."""
    print("Scheduler thread active.")
    schedule.every().monday.to().friday.at("08:30").do(morning_login)
    schedule.every().monday.to().friday.at("15:30").do(stop_scanner_job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

@app.route("/")
def home():
    return "Server is Live. Scanner and Scheduler are running."

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Action Required</h3><p><a href='{login_url}'>Log into Zerodha Kite</a></p>"

    try:
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        with open("access_token.txt", "w") as f:
            f.write(access_token)
        start_scanner_if_token_exists()
        return "<h1>Success!</h1><p>Scanner is now running.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>Login failed: {str(e)}</p>"

if __name__ == "__main__":
    # Start the scheduler in a background thread
    sched_thread = threading.Thread(target=run_scheduler)
    sched_thread.daemon = True
    sched_thread.start()
    
    # Attempt to start scanner immediately on boot
    start_scanner_if_token_exists()
    
    # Use the port from environment variables, provided by Railway
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
