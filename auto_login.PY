from kiteconnect import KiteConnect
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
import time
import os
from env_config import USER_ID, PASSWORD, API_KEY, API_SECRET, TOTP_SECRET

def get_automated_token():
    # Setup Kite
    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    # Headless Selenium (REQUIRED for Railway)
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    try:
        print(f"Opening Login URL for User: {USER_ID}")
        driver.get(login_url)
        time.sleep(3)

        # Enter User ID and Password
        driver.find_element(By.XPATH, "//input[@type='text']").send_keys(USER_ID)
        driver.find_element(By.XPATH, "//input[@type='password']").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        time.sleep(3)

        # Generate TOTP
        totp = pyotp.TOTP(TOTP_SECRET)
        token = totp.now()
        
        # Enter TOTP
        driver.find_element(By.XPATH, "//input[@type='number']").send_keys(token)
        time.sleep(10) # Wait for redirect

        # Get request_token from the redirected URL
        current_url = driver.current_url
        if "request_token=" in current_url:
            request_token = current_url.split("request_token=")[1].split("&")[0]
            
            # Exchange for access_token
            data = kite.generate_session(request_token, api_secret=API_SECRET)
            access_token = data["access_token"]

            print(f"Login Success! Access Token: {access_token}")
            
            # Save token for other scripts
            with open("access_token.txt", "w") as f:
                f.write(access_token)

            return access_token
        else:
            print(f"Failed to find request_token in URL: {current_url}")
            return None

    except Exception as e:
        print(f"Login Failed Error: {e}")
        return None
    finally:
        driver.quit()

if __name__ == "__main__":
    get_automated_token()
