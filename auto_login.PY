import time
import pyotp
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from env_config import API_KEY, USER_ID, PASSWORD, TOTP_SECRET

def get_automated_token():
    print("🚀 Starting automated login process...")
    
    # 1. Setup Chrome Options for Railway (Headless Mode)
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    # 2. Initialize Driver with Auto-Version Matching
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    try:
        # 3. Navigate to Zerodha Login URL
        login_url = f"https://kite.zerodha.com/connect/login?api_key={API_KEY}&v=3"
        driver.get(login_url)
        wait = WebDriverWait(driver, 15)

        # 4. Enter User ID and Password
        print(f"Logging in for User: {USER_ID}")
        user_input = wait.until(EC.presence_of_element_located((By.ID, "userid")))
        user_input.send_keys(USER_ID)
        
        pass_input = driver.find_element(By.ID, "password")
        pass_input.send_keys(PASSWORD)
        
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

        # 5. Handle TOTP (2FA)
        print("Generating TOTP...")
        totp = pyotp.TOTP(TOTP_SECRET.replace(" ", ""))
        token_code = totp.now()
        
        # Wait for TOTP input field (sometimes it's a single box, sometimes 6 boxes)
        # Zerodha's current UI usually uses a specific type/selector for TOTP
        totp_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='number'] | //input[@autofocus='autofocus']")))
        totp_input.send_keys(token_code)
        
        # 6. Capture the Request Token from the URL
        print("Waiting for redirect to capture Request Token...")
        # We wait for the URL to contain 'request_token='
        wait.until(EC.url_contains("request_token="))
        
        current_url = driver.current_url
        request_token = current_url.split("request_token=")[1].split("&")[0]
        
        print(f"✅ Success! Request Token captured: {request_token}")
        
        # Save the token to a temporary file so token_server.py can use it
        with open("request_token.txt", "w") as f:
            f.write(request_token)
            
        return request_token

    except Exception as e:
        print(f"❌ Auto-Login Failed: {str(e)}")
        # Save a screenshot for debugging on Railway if it fails
        driver.save_screenshot("login_error.png")
        return None
        
    finally:
        driver.quit()

if __name__ == "__main__":
    get_automated_token()
