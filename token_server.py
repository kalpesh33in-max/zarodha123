from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading
import os

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

@app.route("/")
def home():
    return "Server is Live. <a href='/login'>Click here to start scanner</a>"

@app.route("/login")
def login():
    # Try to get token from standard request arguments
    request_token = request.args.get("request_token")
    
    # If not in arguments, try to parse from the raw URL (case of missing ?)
    if not request_token:
        # Check if the token is present in the full URL path
        if "request_token=" in request.url:
            try:
                # Split and extract the token value
                request_token = request.url.split("request_token=")[1].split("&")[0]
            except:
                pass

    if not request_token:
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return f"<h3>Action Required</h3><p>To start, please <a href='{login_url}'>Log into Zerodha Kite</a></p>"

    try:
        # Use the request_token to generate a session
        data = kite.generate_session(request_token, API_SECRET)
        kite.set_access_token(data["access_token"])

        # Start scanner in a background thread
        scanner_thread = threading.Thread(target=run_scanner, args=(kite,))
        scanner_thread.daemon = True
        scanner_thread.start()

        return "<h1>Success!</h1><p>Scanner is now running in the background. Check Telegram for updates.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>Login failed: {str(e)}</p><p><a href='/login'>Try logging in again</a></p>"

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
