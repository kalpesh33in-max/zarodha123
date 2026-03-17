import os

# ================= ZERODHA =================
API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

USER_ID = os.getenv("KITE_USER_ID")
PASSWORD = os.getenv("KITE_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET")

# ================= TELEGRAM TOKENS =================
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")

TELE_TOKEN_BN = os.getenv("TELE_TOKEN_BN")
TELE_TOKEN_STOCKS = os.getenv("TELE_TOKEN_STOCKS")
TELE_TOKEN_VELOCITY = os.getenv("TELE_TOKEN_VELOCITY")

# ================= TELEGRAM CHAT IDS =================
TELE_CHAT_ID = os.getenv("CHAT_ID")

TELE_CHAT_ID_BN = os.getenv("CHAT_ID_BN")
TELE_CHAT_ID_STOCKS = os.getenv("CHAT_ID_STOCKS")
TELE_CHAT_ID_VELOCITY = os.getenv("CHAT_ID_VELOCITY")
