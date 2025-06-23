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


def bypass_captcha(driver):
    """
    Handle the captcha solving process
    """
    attempts = 0
    img_elem = driver.find_element(By.ID, "img_captcha")
    captcha_screenshot = img_elem.screenshot_as_png
    b64_data = base64.b64encode(captcha_screenshot).decode("utf-8")        
    
    print("Solving captcha with CapSolver...")
    result = solve_captcha(b64_data)
    
    if not result:
        return False
    
    bypassed = False
    while not bypassed:
        print(f"Captcha attempt {attempts + 1}: {result}")
        secret_input = driver.find_element(By.ID, "secret")
        secret_input.clear()
        secret_input.send_keys(str(result))
        driver.execute_script("submitCaptcha();")
        
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
    
    return bypassed


def handle_login(driver):
    """
    Handle the login process after captcha
    """
    print("Waiting for page to load...")
    
    # Accept cookies if they're still up
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        )
        print("Accepting cookies...")
        btn.click()
    except:
        print("No cookie banner found, continuing...")
    
    # Wait for email/password fields to be visible
    print("Looking for login form...")
    wait = WebDriverWait(driver, 2)
    email_field = wait.until(EC.visibility_of_element_located((By.ID, "loginID")))
    password_field = driver.find_element(By.ID, "password")
    
    if email_field.is_displayed() and password_field.is_displayed():
        print("Login form found, entering credentials...")
        
        # Fill in credentials
        email = os.getenv("LOGIN_EMAIL")
        password = os.getenv("LOGIN_PASSWORD")
        
        if email and password:
            email_field.clear()
            email_field.send_keys(email)
            
            password_field.clear()
            password_field.send_keys(password)
            # Hit Enter in the password field to submit
            password_field.send_keys(Keys.RETURN)
            print(" Enter key pressed to password field!")
            
            time.sleep(7)
            return True
        else:
            print("No email/password found in environment variables!")
            print("Please set LOGIN_EMAIL and LOGIN_PASSWORD in your .env file")
            return False
    else:
        print("Login form fields not visible, continuing...")
        return True


def save_page_html(driver):
    """
    Save the current page HTML to file
    """
    print("Getting final page HTML...")
    result_html = driver.page_source
    
    with open("result_page.html", "w", encoding="utf-8") as f:
        f.write(result_html)
    print("\nResult page saved to 'result_page.html'")


def make_api_request(driver):
    """
    Extract cookies and make API request to catalog endpoint
    """
    print("\nExtracting session cookies for API requests...")
    cookies = driver.get_cookies()
    
    # Create a requests session with the cookies from the browser
    session = requests.Session()
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
    
    # Set required headers based on the endpoint requirements
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en',
        'referer': 'https://ticketsale.wimbledon.com/secured/content',
        'sec-ch-ua': '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
        'x-api-key': '344152a6-fa57-4e09-951f-96b8a38927d9',
        'X-Secutix-Host': 'ticketsale.wimbledon.com'
    }
    
    # Try to extract CSRF token from page if available
    try:
        csrf_token = driver.execute_script("return window.csrfToken || document.querySelector('meta[name=\"csrf-token\"]')?.content")
        if csrf_token:
            headers['x-csrf-token'] = csrf_token
            print(f"Found CSRF token: {csrf_token[:20]}...")
    except:
        print("No CSRF token found, proceeding without it...")
    
    # Make the API request
    api_url = "https://ticketsale.wimbledon.com/tnwr/v1/catalog"
    params = {
        'maxPerformances': 50,
        'maxTimeslots': 50,
        'maxPerformanceDays': 3,
        'maxTimeslotDays': 3,
        'includeMetadata': 'true'
    }
    
    print(f"\nMaking API request to: {api_url}")
    try:
        api_response = session.get(api_url, headers=headers, params=params, verify=False)
        print(f"API Response Status: {api_response.status_code}")
        
        if api_response.status_code == 200:
            # Save the API response
            with open("catalog_response.json", "w", encoding="utf-8") as f:
                f.write(api_response.text)
            print("✅ API response saved to 'catalog_response.json'")
            
            # Print a preview of the response
            try:
                response_data = api_response.json()
                print(f"\nAPI Response Preview:")
                print(f"Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}")
                if isinstance(response_data, dict) and len(response_data) > 0:
                    for key, value in list(response_data.items())[:3]:  # Show first 3 keys
                        print(f"  {key}: {str(value)[:100]}{'...' if len(str(value)) > 100 else ''}")
            except:
                print("Response is not valid JSON or too large to preview")
        else:
            print(f"❌ API request failed with status {api_response.status_code}")
            print(f"Response: {api_response.text[:500]}...")
            
    except Exception as api_error:
        print(f"❌ Error making API request: {api_error}")


def setup_browser():
    """
    Configure and return Chrome driver
    """
    options = Options()
    print("Starting captcha solver...")
    options.headless = True
    return webdriver.Chrome(options=options)


def main():    
    driver = setup_browser()
    
    try:
        driver.get("https://ticketsale.wimbledon.com/content")
        
        # Step 1: Bypass captcha
        if not bypass_captcha(driver):
            print("❌ Failed to solve captcha after all attempts")
            return
        
        # Step 2: Handle login process
        if not handle_login(driver):
            print("❌ Login process failed")
            return
        
        # Step 3: Save the resulting page HTML
        save_page_html(driver)
        
        # Step 4: Make API request using session cookies
        make_api_request(driver)
            
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        print("Browser will remain open...")
        print("Press Enter to close the browser and exit...")
        input()  # Wait for user input before closing
        driver.quit()  # Now close the browser when user is ready


if __name__ == "__main__":
    main()