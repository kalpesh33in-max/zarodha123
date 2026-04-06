from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading
import os

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

def start_scanner_thread():
    if os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
        if token:
            print("Access token found. Starting scanner thread...", flush=True)
            kite.set_access_token(token)
            t = threading.Thread(target=run_scanner, args=(kite,))
            t.daemon = True
            t.start()

@app.route("/")
def home():
    return "<h3>Kite Scanner: Server is Live</h3>"

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<a href='{login_url}'>Click here to Login</a>"
    
    try:
        # If this fails with 'Invalid Session', the token was used or expired
        data = kite.generate_session(request_token, API_SECRET)
        with open("access_token.txt", "w") as f:
            f.write(data["access_token"])
        start_scanner_thread()
        return "<h1>Success!</h1><p>Scanner has been started.</p>"
    except Exception as e:
        print(f"Login Failure: {e}", flush=True)
        return f"<h1>Error</h1><p>{str(e)}</p>"

if __name__ == "__main__":
    start_scanner_thread()
    # Bind to 0.0.0.0 and dynamic Railway PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
