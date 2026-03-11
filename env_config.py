import os

# Zerodha Credentials (Matching your Railway names)
API_KEY = os.getenv("KITE_API_KEY", "YOUR_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET", "YOUR_API_SECRET")

# Automation Credentials
USER_ID = os.getenv("KITE_USER_ID", "YOUR_USER_ID")
PASSWORD = os.getenv("KITE_PASSWORD", "YOUR_PASSWORD")
TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "YOUR_TOTP_SECRET")

# Telegram Credentials (Matching your Railway names)
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN", "8587757379:AAEa1zQmNAN8xcYLaQlXzsMzqph3bNqUpgg")
TELE_CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
