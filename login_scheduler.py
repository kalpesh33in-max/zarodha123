import schedule
import time
from kiteconnect import KiteConnect
from env_config import API_KEY

kite = KiteConnect(api_key=API_KEY)

def send_login():

    print("Login here:")
    print(kite.login_url())

# Schedule login URL reminder every day at 08:30 (Market pre-open)
schedule.every().day.at("08:30").do(send_login)

print("Login Scheduler started. Waiting for 08:30 IST...")

while True:

    schedule.run_pending()

    time.sleep(1)
