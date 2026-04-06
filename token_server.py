from flask import Flask
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading, os

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

def start_auto():
    if os.path.exists("access_token.txt"):
        with open("access_token.txt", "r") as f:
            token = f.read().strip()
        if token:
            kite.set_access_token(token)
            t = threading.Thread(target=run_scanner, args=(kite,))
            t.daemon = True
            t.start()

@app.route("/")
def home(): return "Scanner active."

if __name__ == "__main__":
    start_auto()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
