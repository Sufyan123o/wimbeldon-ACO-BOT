import requests
import base64
import os
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

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
    print("Starting captcha solver...")
    
    # Configure headless Chrome
    options = Options()
    options.headless = True
    
    driver = webdriver.Chrome(options=options)
    
    try:
        # Navigate to the target page
        print("Loading website...")
        driver.get("https://ticketsale.wimbledon.com/content")
        
        # Locate the captcha image element and get its 'src'
        print("Finding captcha image...")
        img_elem = driver.find_element(By.ID, "img_captcha")
        img_src = img_elem.get_attribute("src")
        
        # Ensure full URL if 'src' is relative
        if img_src.startswith("/"):
            img_src = "https://ticketsale.wimbledon.com" + img_src
        
        print(f"Captcha image URL: {img_src}")
        
        # Create a requests session and transfer cookies from Selenium
        session = requests.Session()
        for cookie in driver.get_cookies():
            session.cookies.set(cookie['name'], cookie['value'])
        
        # Download the captcha image
        print("Downloading captcha image...")
        resp = session.get(img_src, verify=False)
        resp.raise_for_status()
        
        # Encode the image bytes into a Base64 string
        b64_data = base64.b64encode(resp.content).decode("utf-8")
        
        print("Solving captcha with CapSolver...")
        result = solve_captcha(b64_data)
        
        if result:
            print(f"Captcha solved! Result: {result}")
        else:
            print("Failed to solve captcha")
    
    except Exception as e:
        print(f"Error: {e}")
    
    finally:
        driver.quit()


if __name__ == "__main__":
    main()