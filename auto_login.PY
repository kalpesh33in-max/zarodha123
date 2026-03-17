from kiteconnect import KiteConnect
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
from env_config import USER_ID, PASSWORD, API_KEY, API_SECRET, TOTP_SECRET

def get_automated_token():
    kite = KiteConnect(api_key=API_KEY)
    login_url = kite.login_url()

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    wait = WebDriverWait(driver, 15)

    try:
        print(f"Logging in User: {USER_ID}")
        driver.get(login_url)
        
        # ID and Password
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']"))).send_keys(USER_ID)
        driver.find_element(By.XPATH, "//input[@type='password']").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        
        # 2FA / TOTP
        print("Entering TOTP...")
        totp = pyotp.TOTP(TOTP_SECRET.replace(" ", ""))
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='number']"))).send_keys(totp.now())
        
        # Capture Redirect
        wait.until(EC.url_contains("request_token="))
        request_token = driver.current_url.split("request_token=")[1].split("&")[0]
        
        data = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = data["access_token"]

        with open("access_token.txt", "w") as f:
            f.write(access_token)

        print("Login Successful!")
        return access_token

    except Exception as e:
        print(f"Login Error: {e}")
        driver.save_screenshot("error.png")
        return None
    finally:
        driver.quit()
