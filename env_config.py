import os

# ================= ZERODHA CREDENTIALS =================
# These are pulled from your Railway Environment Variables
API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

# Automation Credentials for auto_login.py
USER_ID = os.getenv("KITE_USER_ID")
PASSWORD = os.getenv("KITE_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET")

# ================= TELEGRAM TOKENS =================
# Primary Bot Token
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Optional: Specific tokens for different alert types 
# (Defaults to main TELE_TOKEN if specific ones aren't set)
TELE_TOKEN_BN = os.getenv("TELE_TOKEN_BN") or TELE_TOKEN
TELE_TOKEN_STOCKS = os.getenv("TELE_TOKEN_STOCKS") or TELE_TOKEN
TELE_TOKEN_VELOCITY = os.getenv("TELE_TOKEN_VELOCITY") or TELE_TOKEN

# ================= TELEGRAM CHAT IDS =================
# These must be actual numbers (e.g., 12345678 or -10012345678)
TELE_CHAT_ID = os.getenv("CHAT_ID")

TELE_CHAT_ID_BN = os.getenv("CHAT_ID_BN") or TELE_CHAT_ID
TELE_CHAT_ID_STOCKS = os.getenv("CHAT_ID_STOCKS") or TELE_CHAT_ID
TELE_CHAT_ID_VELOCITY = os.getenv("CHAT_ID_VELOCITY") or TELE_CHAT_ID

# ================= VALIDATION (Optional) =================
# This will print a warning in your Railway logs if critical info is missing
if not TELE_TOKEN:
    print("⚠️ WARNING: TELEGRAM_TOKEN is not set in Environment Variables!")
if not TELE_CHAT_ID:
    print("⚠️ WARNING: CHAT_ID is not set in Environment Variables!")
