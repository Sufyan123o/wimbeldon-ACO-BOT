import requests
import base64
import os
import urllib3
import time
import json
from datetime import datetime
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
    session, headers = create_session_from_driver(driver)
    
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
            
            return session, headers
        else:
            print(f"❌ API request failed with status {api_response.status_code}")
            print(f"Response: {api_response.text[:500]}...")
            return None, None
            
    except Exception as api_error:
        print(f"❌ Error making API request: {api_error}")
        return None, None

def send_discord_webhook(day_name, buy_link, advantage_name):
    """
    Send Discord webhook notification for available tickets
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    
    if not webhook_url:
        print("⚠️ No Discord webhook URL found in environment variables!")
        print("Please set DISCORD_WEBHOOK_URL in your .env file")
        return False
    
    embed = {
        "title": "🎾 Wimbledon Tickets Available!",
        "description": f"**{day_name}** tickets are now available!",
        "color": 0x00ff00,  # Green color
        "fields": [
            {
                "name": "Event",
                "value": day_name,
                "inline": True
            },
            {
                "name": "Advantage Type",
                "value": advantage_name,
                "inline": True
            },
            {
                "name": "Purchase Link",
                "value": f"[Buy Tickets]({buy_link})",
                "inline": False
            }
        ],
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "Wimbledon Ticket Monitor"
        }
    }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print(f"✅ Discord notification sent for {day_name}")
            return True
        else:
            print(f"❌ Failed to send Discord notification: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error sending Discord notification: {e}")
        return False


def send_test_webhook():
    """
    Send a test Discord webhook notification to verify setup
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    
    if not webhook_url:
        print("⚠️ No Discord webhook URL found in environment variables!")
        print("Please set DISCORD_WEBHOOK_URL in your .env file")
        return False
    
    embed = {
        "title": "🧪 Wimbledon Monitor Test",
        "description": "Test notification - Your webhook is working correctly!",
        "color": 0x0099ff,  # Blue color
        "fields": [
            {
                "name": "Status",
                "value": "✅ Webhook Connected",
                "inline": True
            },
            {
                "name": "Monitoring",
                "value": "Ready to detect ticket availability",
                "inline": True
            }
        ],
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {
            "text": "Wimbledon Ticket Monitor - Test Message"
        }
    }
    
    payload = {
        "embeds": [embed]
    }
    
    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print("✅ Test Discord notification sent successfully!")
            return True
        else:
            print(f"❌ Failed to send test Discord notification: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error sending test Discord notification: {e}")
        return False


def monitor_performances(session, headers, known_states=None):
    """
    Monitor performances for availability changes in Centre Court events
    """
    if known_states is None:
        known_states = {}
    
    api_url = "https://ticketsale.wimbledon.com/tnwr/v1/catalog"
    params = {
        'maxPerformances': 50,
        'maxTimeslots': 50,
        'maxPerformanceDays': 3,
        'maxTimeslotDays': 3,
        'includeMetadata': 'true'
    }
    
    try:
        response = session.get(api_url, headers=headers, params=params, verify=False)
        
        if response.status_code != 200:
            print(f"❌ API request failed with status {response.status_code}")
            return known_states
        
        data = response.json()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{current_time}] Checking for availability changes...")
        
        # Navigate through the JSON structure
        sections = data.get('sections', [])
        changes_found = False
        
        for section in sections:
            clusters = section.get('clusters', [])
            for cluster in clusters:
                items = cluster.get('items', [])
                for item in items:
                    product = item.get('product', {})
                    performances = product.get('performances', [])
                    
                    for performance in performances:
                        name = performance.get('name', {}).get('en', '')
                        
                        # Check if this is a Centre Court event
                        if 'Centre Court Day' in name:
                            perf_id = performance.get('performanceId')
                            buy_link = performance.get('action', {}).get('buy', '')
                            advantages = performance.get('advantages', [])
                            
                            # Check each advantage for availability changes
                            for advantage in advantages:
                                advantage_id = advantage.get('id')
                                advantage_name = advantage.get('name', {}).get('en', '')
                                current_availability = advantage.get('availability', 'NONE')
                                
                                # Create unique key for this advantage
                                state_key = f"{perf_id}_{advantage_id}"
                                
                                # Check if this is a new state or changed from NONE
                                previous_availability = known_states.get(state_key, 'NONE')
                                
                                if previous_availability == 'NONE' and current_availability != 'NONE':
                                    print(f"🎉 RESTOCK!")
                                    print(f"   Event: {name}")
                                    print(f"   Advantage: {advantage_name}")
                                    print(f"   Status: {previous_availability} → {current_availability}")
                                    print(f"   Buy Link: {buy_link}")
                                    
                                    # Send Discord notification
                                    send_discord_webhook(name, buy_link, advantage_name)
                                    changes_found = True
                                
                                # Update known state
                                known_states[state_key] = current_availability
        
        if not changes_found:
            print("   No availability changes detected.")
            
        return known_states
        
    except Exception as e:
        print(f"❌ Error during monitoring: {e}")
        return known_states


def start_monitoring(session, headers):
    """
    Start the continuous monitoring loop
    """
    print("\n🔍 Starting Wimbledon ticket monitoring...")
    print("Monitoring Centre Court events for availability changes...")
    print("Checking every 30 seconds. Press Ctrl+C to stop.\n")
    
    known_states = {}
    
    try:
        while True:
            known_states = monitor_performances(session, headers, known_states)
            time.sleep(30)  # Wait 30 seconds before next check
            
    except KeyboardInterrupt:
        print("\n⏹️ Monitoring stopped by user.")
    except Exception as e:
        print(f"\n❌ Monitoring error: {e}")


def create_session_from_driver(driver):
    """
    Create a requests session with cookies from the browser driver
    """
    cookies = driver.get_cookies()
    session = requests.Session()
    
    for cookie in cookies:
        session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
    
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
    
    return session, headers




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
          # Step 4: Make initial API request and get session
        session, headers = make_api_request(driver)
        
        if not session or not headers:
            print("❌ Failed to create session for monitoring")
            return
        
        # Step 5: Send test webhook and start monitoring
        print("\n" + "="*50)
        print("✅ Setup complete! Session established.")
        print("="*50)
        
        print("\nSending test Discord notification...")
        send_test_webhook()
        
        print("\nStarting monitoring automatically...")
        start_monitoring(session, headers)
            
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        print("\nBrowser will remain open for 10 seconds...")
        time.sleep(10)
        driver.quit()  # Close the browser


if __name__ == "__main__":
    main()