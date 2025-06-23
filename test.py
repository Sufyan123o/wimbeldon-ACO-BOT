import requests
import base64
import os
import urllib3
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# Your CapSolver API key
api_key = os.getenv("API_KEY")


def solve_captcha(base64_image):
    """
    Solve a base64 encoded captcha using CapSolver
    Args:
        base64_image: Base64 encoded image string
    Returns:
        Recognition result
    """
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "ImageToTextTask",
            "module": "module_016",  # Use number module for number recognition
            "body": base64_image,  # Single image
        },
    }
    
    response = requests.post("https://api.capsolver.com/createTask", json=payload)
    result = response.json()
    
    if result.get("errorId") == 0 and "solution" in result:
        solution = result["solution"]
        if "text" in solution:
            return solution["text"]
        elif "answers" in solution:
            return solution["answers"]
    else:
        print(f"CapSolver Error: {result}")
        return None


def main():    
    # Configure fully headless Chrome
    options = Options()
    print("Starting captcha solver...")
    # Configure headless Chrome
    options.headless = True
    
    driver = webdriver.Chrome(options=options)
    attempts = 0
    try:
        driver.get("https://ticketsale.wimbledon.com/content")
        img_elem = driver.find_element(By.ID, "img_captcha")
        captcha_screenshot = img_elem.screenshot_as_png
        b64_data = base64.b64encode(captcha_screenshot).decode("utf-8")        
        with open("image.txt", "w") as f:
            f.write(b64_data)
        print("Solving captcha with CapSolver...")
        result = solve_captcha(b64_data)
        
        if result:
            bypassed = False
            while not bypassed:
                print(f"Captcha attempt {attempts + 1}: {result}")
                secret_input = driver.find_element(By.ID, "secret")
                secret_input.clear()
                secret_input.send_keys(str(result))
                driver.execute_script("submitCaptcha();")
                
                # time.sleep(3)  # Wait for response
                
                # Check if we're past the captcha (look for next page elements)
                try:
                    # If we see the action button or login form, captcha worked
                    if (driver.find_elements(By.ID, "actionButton") or 
                        driver.find_elements(By.ID, "loginID")):
                        bypassed = True
                        print("✅ Captcha bypassed!")
                except:
                    pass
                
                if not bypassed:
                    attempts += 1
                    print("Captcha failed, retrying...")
                    # Get new captcha image
                    img_elem = driver.find_element(By.ID, "img_captcha")
                    captcha_screenshot = img_elem.screenshot_as_png
                    b64_data = base64.b64encode(captcha_screenshot).decode("utf-8")
                    result = solve_captcha(b64_data)
                    if not result:
                        break
            
            if not bypassed:
                print("❌ Failed to solve captcha after all attempts")
                return
            # Wait and check for ENTER button (appears after timer if there's a waiting room)
            print("Waiting for page to load...")
            
            # Simple check: if ENTER button appears, click it
            try:
                enter_button = driver.find_element(By.ID, "actionButton")
                if enter_button.is_displayed():
                    print("ENTER button found! Clicking it...")
                    enter_button.click()
                    time.sleep(3)
            except:
                print("No ENTER button found, proceeding...")            # 1) Accept cookies if they're still up
            try:
                btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
                )
                print("Accepting cookies...")
                btn.click()
                time.sleep(1)
            except:
                print("No cookie banner found, continuing...")
            
            # 2) Wait for email/password fields to be visible
            print("Looking for login form...")
            wait = WebDriverWait(driver, 3)
            email_field = wait.until(EC.visibility_of_element_located((By.ID, "loginID")))
            password_field = driver.find_element(By.ID, "password")
            
            if email_field.is_displayed() and password_field.is_displayed():
                print("Login form found, entering credentials...")
                
                # 3) Fill in creds exactly as before
                email = os.getenv("LOGIN_EMAIL")
                password = os.getenv("LOGIN_PASSWORD")
                
                if email and password:
                    email_field.clear()
                    email_field.send_keys(email)
                    
                    password_field.clear()
                    password_field.send_keys(password)
                    # 4) Hit Enter in the password field to submit
                    print("Submitting login form by pressing Enter...")
                    password_field.send_keys(Keys.RETURN)
                    print("✅ Sent Enter key to password field!")
                    
                    time.sleep(3)
                else:
                    print("No email/password found in environment variables!")
                    print("Please set LOGIN_EMAIL and LOGIN_PASSWORD in your .env file")
            else:
                print("Login form fields not visible, continuing...")
            
            # Get the HTML of the resulting page
            print("Getting final page HTML...")
            result_html = driver.page_source
            
            print("="*50)
            print("RESULT PAGE HTML:")
            print("="*50)
            # print(result_html)
            
            # Optionally save to file
            with open("result_page.html", "w", encoding="utf-8") as f:
                f.write(result_html)
            print("\nResult page saved to 'result_page.html'")
            
        else:
            print("Failed to solve captcha")
    
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        print("Browser will remain open...")
        print("Press Enter to close the browser and exit...")
        input()  # Wait for user input before closing
        driver.quit()  # Now close the browser when user is ready


if __name__ == "__main__":
    main()