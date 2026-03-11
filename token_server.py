from flask import Flask, request
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
    return "Zerodha Scanner Server is running. Go to /login to start."

@app.route("/login")
def login():
    request_token = request.args.get("request_token")

    if not request_token:
        login_url = kite.login_url()
        return f"Request token missing. <a href='{login_url}'>Click here to login</a>"

    try:
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        kite.set_access_token(access_token)

        # Start scanner in a background thread to not block Flask
        scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
        scanner_thread.daemon = True
        scanner_thread.start()

        return "Login successful! Scanner started in the background."
    except Exception as e:
        return f"Login failed: {str(e)}"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
