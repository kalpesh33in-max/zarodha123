import os

# Zerodha Credentials (Matching your Railway names)
API_KEY = os.getenv("KITE_API_KEY", "YOUR_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_API_SECRET")

# Automation Credentials
USER_ID = os.getenv("KITE_USER_ID", "YOUR_USER_ID")
PASSWORD = os.getenv("KITE_PASSWORD", "YOUR_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "YOUR_TOTP_SECRET")

# Telegram Credentials
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELE_TOKEN_BN = os.getenv("TELE_TOKEN_BN", TELE_TOKEN)
TELE_TOKEN_STOCKS = os.getenv("TELE_TOKEN_STOCKS", TELE_TOKEN)
TELE_TOKEN_VELOCITY = os.getenv("TELE_TOKEN_VELOCITY", TELE_TOKEN)

TELE_CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
TELE_CHAT_ID_BN = os.getenv("CHAT_ID_BN", TELE_CHAT_ID)
TELE_CHAT_ID_STOCKS = os.getenv("CHAT_ID_STOCKS", TELE_CHAT_ID)
TELE_CHAT_ID_VELOCITY = os.getenv("CHAT_ID_VELOCITY", TELE_CHAT_ID)

# Matrix / Element X Credentials
MATRIX_HOMESERVER = os.getenv("MATRIX_HOMESERVER", "https://matrix.org")
MATRIX_ACCESS_TOKEN = os.getenv("MATRIX_ACCESS_TOKEN", "")
MATRIX_USER = os.getenv("MATRIX_USER", "")
MATRIX_PASS = os.getenv("MATRIX_PASS", "")
MATRIX_ROOM_ID = os.getenv("MATRIX_ROOM_ID", "") # Default Room ID
MATRIX_ROOM_ID_BN = os.getenv("MATRIX_ROOM_ID_BN", MATRIX_ROOM_ID)
