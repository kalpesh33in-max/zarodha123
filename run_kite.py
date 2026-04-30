app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

# Global handles
scanner_thread = None
flow_engine = None
scanner_lock = threading.Lock()

# Flag to ensure background tasks are started only once
_background_tasks_started = False

def start_background_tasks_if_needed():
    global _background_tasks_started
    if _background_tasks_started:
        return

    print("Starting background tasks (scheduler and scanner bootup)...")

    # 1. Start Scheduler Thread (Background tasks)
    sched_thread = threading.Thread(target=run_scheduler_loop, daemon=True)
    sched_thread.start()

    # 2. Start Scanner Boot Thread (if auto-start is enabled)
    if AUTO_START_SCANNER:
        boot_thread = threading.Thread(
            target=validate_and_start_scanner, 
            args=("Initial Boot",), 
            daemon=True
        )
        boot_thread.start()
    
    _background_tasks_started = True

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
        if not token: return False

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
    # Automated login removed for speed. User will click link in Telegram.

def run_scheduler_loop():
    print("Background Scheduler Active.")
    schedule.every().monday.to().friday.at("08:30").do(morning_task)
    while True:
        schedule.run_pending()
        time.sleep(10)

# --- Flask Routes ---

@app.route("/")
def home():
    start_background_tasks_if_needed() # Ensure tasks are started when any route is hit
    status = "RUNNING" if (scanner_thread and scanner_thread.is_alive()) else "STOPPED"
    return f"<h3>Kite Scanner Status: {status}</h3><p>Server Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}</p>"

@app.route("/login")
def login():
    start_background_tasks_if_needed() # Ensure tasks are started
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
# For Gunicorn, the app is imported and routes are hit, triggering `start_background_tasks_if_needed()`
if __name__ == "__main__":
    print(f"Starting Web Server directly via Flask dev server on port {os.getenv("PORT", 8080)}...")
    start_background_tasks_if_needed()
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
