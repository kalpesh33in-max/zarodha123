from kiteconnect import KiteConnect
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
import time
import os

# Zerodha Credentials (REPLACE WITH YOURS)
USER_ID = "YOUR_USER_ID"
PASSWORD = "YOUR_PASSWORD"
API_KEY = "YOUR_API_KEY"
API_SECRET = "YOUR_API_SECRET"
TOTP_SECRET = "YOUR_32_CHAR_TOTP_SECRET"

def get_automated_token():
    # Setup Kite
    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    # Headless Selenium (Runs in background)
    chrome_options = Options()
    # chrome_options.add_argument("--headless") # Uncomment to run invisibly
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    try:
        driver.get(login_url)
        time.sleep(2)

        # Enter User ID and Password
        driver.find_element(By.XPATH, "//input[@type='text']").send_keys(USER_ID)
        driver.find_element(By.XPATH, "//input[@type='password']").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        time.sleep(2)

        # Generate TOTP using pyotp
        totp = pyotp.TOTP(TOTP_SECRET)
        token = totp.now()
        
        # Enter TOTP
        driver.find_element(By.XPATH, "//input[@type='number']").send_keys(token)
        time.sleep(5) # Wait for redirect

        # Get request_token from the redirected URL
        current_url = driver.current_url
        request_token = current_url.split("request_token=")[1].split("&")[0]
        
        # Exchange for access_token
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]

        print(f"Login Success! Access Token: {access_token}")
        
        # Save token for other scripts
        with open("access_token.txt", "w") as f:
            f.write(access_token)

        return access_token

    except Exception as e:
        print(f"Login Failed: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    get_automated_token()
