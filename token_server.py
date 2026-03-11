from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading
import os

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

def start_scanner_if_token_exists():
    """Checks for access_token.txt and starts the scanner automatically."""
    if os.path.exists("access_token.txt"):
        try:
            with open("access_token.txt", "r") as f:
                token = f.read().strip()
            
            if token:
                print(f"Found saved token. Starting scanner automatically...")
                kite.set_access_token(token)
                
                # Start scanner in a background thread
                scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
                scanner_thread.daemon = True
                scanner_thread.start()
                return True
        except Exception as e:
            print(f"Failed to auto-start scanner: {e}")
    return False

@app.route("/")
def home():
    return "Server is Live. Scanner will start automatically if token is found."

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    if not request_token and "request_token=" in request.url:
        request_token = request.url.split("request_token=")[1].split("&")[0]

    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Action Required</h3><p>To start, please <a href='{login_url}'>Log into Zerodha Kite</a></p>"

    try:
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        
        # Save token for auto-restart
        with open("access_token.txt", "w") as f:
            f.write(access_token)

        scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
        scanner_thread.daemon = True
        scanner_thread.start()

        return "<h1>Success!</h1><p>Scanner is now running.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>Login failed: {str(e)}</p>"

if __name__ == "__main__":
    # Attempt to start scanner immediately on boot
    start_scanner_if_token_exists()
    
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
else:
    # This runs when Gunicorn imports the file
    start_scanner_if_token_exists()
