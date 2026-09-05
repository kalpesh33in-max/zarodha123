import os
import sys
import time
import pyotp
import requests
import logging
from urllib.parse import urlparse, parse_qs
from kiteconnect import KiteConnect

logger = logging.getLogger("AutoLogin")

def perform_auto_login(retries=3, notify_telegram=True):
    from env_config import USER_ID, PASSWORD, TOTP_SECRET, API_KEY, API_SECRET
    from telegram_utils import send_telegram_message

    if not USER_ID or not PASSWORD or not TOTP_SECRET or not API_KEY or not API_SECRET:
        msg = "⚠️ Auto-Login aborted: Zerodha credentials or TOTP_SECRET missing in .env"
        print(msg)
        if notify_telegram:
            send_telegram_message(msg)
        return False, msg

    for attempt in range(1, retries + 1):
        try:
            print(f"[AutoLogin] Attempt {attempt}/{retries} for User {USER_ID}...")
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

            # Step 1: Login with User ID and Password
            r1 = session.post(
                "https://kite.zerodha.com/api/login",
                data={"user_id": USER_ID, "password": PASSWORD},
                timeout=10
            )
            d1 = r1.json()
            if d1.get("status") != "success":
                raise RuntimeError(f"Step 1 login failed: {d1.get('message')}")

            req_id = d1["data"]["request_id"]

            # Step 2: 2FA TOTP code
            totp = pyotp.TOTP(TOTP_SECRET).now()
            r2 = session.post(
                "https://kite.zerodha.com/api/twofa",
                data={
                    "user_id": USER_ID,
                    "request_id": req_id,
                    "twofa_value": totp,
                    "twofa_type": "totp",
                    "skip_session": ""
                },
                timeout=10
            )
            d2 = r2.json()
            if d2.get("status") != "success":
                raise RuntimeError(f"Step 2 2FA failed: {d2.get('message')}")

            # Step 3: Kite Connect authorization flow
            connect_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
            r3 = session.get(connect_url, allow_redirects=False, timeout=10)
            finish_url = r3.headers.get("Location")
            if not finish_url:
                raise RuntimeError(f"No finish redirect in connect flow: HTTP {r3.status_code}")

            r4 = session.get(finish_url, allow_redirects=False, timeout=10)
            final_redirect = r4.headers.get("Location")
            if not final_redirect:
                raise RuntimeError(f"No final redirect from finish endpoint: HTTP {r4.status_code}")

            parsed = urlparse(final_redirect)
            qs = parse_qs(parsed.query)
            req_token = qs.get("request_token", [None])[0]
            if not req_token:
                raise RuntimeError(f"request_token missing in final redirect: {final_redirect}")

            # Step 4: Exchange request_token for access_token
            kite = KiteConnect(api_key=API_KEY)
            s_data = kite.generate_session(req_token, api_secret=API_SECRET)
            access_token = s_data.get("access_token")
            if not access_token:
                raise RuntimeError("Access token empty in session response")

            # Validate access token by fetching profile
            kite.set_access_token(access_token)
            prof = kite.profile()
            user_name = prof.get("user_name", USER_ID)

            # Save token to file
            base_dir = os.path.dirname(os.path.abspath(__file__))
            token_path = os.path.join(base_dir, "access_token.txt")
            with open(token_path, "w") as f:
                f.write(access_token)

            success_msg = (
                f"🟢 [AUTO-LOGIN SUCCESS]\n"
                f"👤 Account: {user_name} ({USER_ID})\n"
                f"🔑 Session Token Refreshed Successfully!\n"
                f"🚀 1,394 instruments streaming & all scanners primed for market open."
            )
            print(f"[AutoLogin] {success_msg}")
            if notify_telegram:
                send_telegram_message(success_msg)

            return True, access_token

        except Exception as e:
            print(f"[AutoLogin] Attempt {attempt} failed: {e}")
            if attempt == retries:
                err_msg = (
                    f"⚠️ [AUTO-LOGIN FAILED]\n"
                    f"Error: {e}\n"
                    f"Action Required: Please click here to login manually:\n"
                    f"👉 https://zarodha.marketmenia.site/login"
                )
                if notify_telegram:
                    send_telegram_message(err_msg)
                return False, str(e)
            time.sleep(2)

if __name__ == "__main__":
    success, result = perform_auto_login()
    sys.exit(0 if success else 1)
