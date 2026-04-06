from flask import Flask, request
from kiteconnect import KiteConnect
from scanner import run_scanner
from env_config import API_KEY, API_SECRET
import threading
import os

app = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)

def start_scanner_if_token_exists():
    """
    Checks if access_token.txt exists and attempts to start 
    the background scanner thread.
    """
    if os.path.exists("access_token.txt"):
        try:
            with open("access_token.txt", "r") as f:
                token = f.read().strip()
            
            if token:
                print(f"Found saved token. Validating and starting scanner...")
                kite.set_access_token(token)
                
                # Start the scanner in a separate background thread
                # so the Flask web server remains responsive.
                t = threading.Thread(target=run_scanner, args=(kite,))
                t.daemon = True
                t.start()
                return True
        except Exception as e:
            print(f"Failed to auto-start scanner: {e}")
    return False

@app.route("/")
def home():
    """Root endpoint to check if the server is running."""
    return "<h1>Kite Scanner Server</h1><p>Status: Online</p><p><a href='/login'>Click here to Login/Re-auth</a></p>"

@app.route("/login")
def login():
    """
    Handles the Zerodha Login flow. 
    1. Redirects to Zerodha if no request_token is present.
    2. Exchanges request_token for access_token if present.
    """
    request_token = request.args.get("request_token")
    
    if not request_token:
        # Step 1: Generate the Zerodha Login URL
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        return (f"<h3>Action Required</h3>"
                f"Valid session not found. <br><br>"
                f"<a href='{login_url}' style='padding:10px; background:blue; color:white; text-decoration:none;'>Log into Zerodha Kite</a>")

    try:
        # Step 2: Exchange request_token for access_token
        data = kite.generate_session(request_token, API_SECRET)
        access_token = data["access_token"]
        
        # Save the token to a file so the scanner can survive restarts
        with open("access_token.txt", "w") as f:
            f.write(access_token)
        
        # Step 3: Start the background scanner
        start_scanner_if_token_exists()
        
        return "<h1>Success!</h1><p>Login successful. The Market Scanner has been started in the background.</p>"
    except Exception as e:
        return f"<h1>Error</h1><p>Login failed: {str(e)}</p><p><a href='/login'>Try Again</a></p>"

# ---------------------------------------------------------
# AUTO-START ON BOOT
# ---------------------------------------------------------
# When Railway starts the container, this immediately tries 
# to resume the scanner if today's token is already saved.
start_scanner_if_token_exists()

if __name__ == "__main__":
    # Use PORT from environment (required for Railway)
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
