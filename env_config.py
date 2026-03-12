import os

# Zerodha Credentials (Matching your Railway names)
API_KEY = os.getenv("KITE_API_KEY", "YOUR_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_API_SECRET")

# Automation Credentials
USER_ID = os.getenv("KITE_USER_ID", "YOUR_USER_ID")
PASSWORD = os.getenv("KITE_PASSWORD", "YOUR_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "YOUR_TOTP_SECRET")

# Telegram Credentials
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "8587757379:AAEa1zQmNAN8xcYLaQlXzsMzqph3bNqUpgg")
TELE_TOKEN_BN = os.getenv("TELE_TOKEN_BN", TELE_TOKEN)
TELE_TOKEN_STOCKS = os.getenv("TELE_TOKEN_STOCKS", TELE_TOKEN)

TELE_CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
TELE_CHAT_ID_BN = os.getenv("CHAT_ID_BN", TELE_CHAT_ID)
TELE_CHAT_ID_STOCKS = os.getenv("CHAT_ID_STOCKS", TELE_CHAT_ID)
