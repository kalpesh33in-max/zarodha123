import schedule
import time
from kiteconnect import KiteConnect

API_KEY="YOUR_API_KEY"

kite = KiteConnect(api_key=API_KEY)

def send_login():

    print("Login here:")
    print(kite.login_url())

schedule.every().day.at("08:00").do(send_login)

while True:

    schedule.run_pending()

    time.sleep(1)
