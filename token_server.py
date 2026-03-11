from flask import Flask, request, redirect
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading
import os

app = Flask(__name__)

# Load API Key from environment or env_config
kite = KiteConnect(api_key=API_KEY)

@app.route("/")
def home():
    return "Zerodha Scanner Server is running. <a href='/login'>Click here to start</a>"

@app.route("/login")
def login():
    request_token = request.args.get("request_token")

    if not request_token:
        # Generate login URL with a proper question mark
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Scanner Not Started</h3><p>To start, please <a href='{login_url}'>Log into Zerodha Kite</a></p>"

    try:
        # Use the request_token to generate a session
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        kite.set_access_token(access_token)

        # Start scanner in a background thread
        scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
        scanner_thread.daemon = True
        scanner_thread.start()

        return "<h1>Login Successful!</h1><p>The scanner is now running in the background. You can close this window. Check Telegram for updates.</p>"
    except Exception as e:
        # If token is already used, show a clear message
        return f"<h1>Login Failed</h1><p>Error: {str(e)}</p><p><a href='/login'>Try logging in again</a></p>"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
