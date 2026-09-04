import os

# Auto-load .env or .env.example
for env_file in [".env", ".env.example"]:
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        except Exception:
            pass

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
TELE_TOKEN_VELOCITY = os.getenv("TELE_TOKEN_VELOCITY", TELE_TOKEN)
TELE_TOKEN_REPORTS = os.getenv("TELE_TOKEN_REPORTS", TELE_TOKEN)

TELE_CHAT_ID = os.getenv("CHAT_ID", "YOUR_CHAT_ID")
TELE_CHAT_ID_BN = os.getenv("CHAT_ID_BN", TELE_CHAT_ID)
TELE_CHAT_ID_VELOCITY = os.getenv("CHAT_ID_VELOCITY", TELE_CHAT_ID)
TELE_CHAT_ID_REPORTS = os.getenv("CHAT_ID_REPORTS", TELE_CHAT_ID)
TELE_CHAT_ID_STOCKS = os.getenv("CHAT_ID_STOCKS", TELE_CHAT_ID)
TELE_TOKEN_STOCKS = os.getenv("TELE_TOKEN_STOCKS", TELE_TOKEN)

# Multi-Timeframe Reversal Channel (Ai scanner allert)
TELE_CHAT_ID_AI_SCANNER = os.getenv("CHAT_ID_AI_SCANNER", "-1004326717783")
TELE_CHAT_ID_REVERSAL = os.getenv("CHAT_ID_REVERSAL", TELE_CHAT_ID_AI_SCANNER)

# BankNifty Radar (Routed to @zarodastock_bot private chat, never to channels)
TELE_TOKEN_RADAR = os.getenv("TELE_TOKEN_RADAR", TELE_TOKEN_STOCKS)
TELE_CHAT_ID_RADAR = os.getenv("CHAT_ID_RADAR", TELE_CHAT_ID_STOCKS)

NSE_HOLIDAYS = {
    "2024-01-26", "2024-03-08", "2024-03-25", "2024-03-29", "2024-04-11", "2024-04-17", "2024-05-01", "2024-05-20", "2024-06-17", "2024-07-17", "2024-08-15", "2024-10-02", "2024-11-01", "2024-11-15", "2024-12-25",
    "2025-01-26", "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10", "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15", "2025-10-02", "2025-10-21", "2025-11-05", "2025-12-25",
    "2026-01-26", "2026-03-03", "2026-04-03", "2026-04-14", "2026-05-01", "2026-08-15", "2026-10-02", "2026-11-08", "2026-12-25"
}

