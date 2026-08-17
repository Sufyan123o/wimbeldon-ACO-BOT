import logging
from seleniumbase import SB
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_undetectable_chrome():
    with SB(uc=True, headless=False) as sb:
        logging.info("Undetectable Chrome started successfully.")
        
        try:
            # Test navigation
            logging.info("Navigating to test site...")
            sb.uc_open_with_reconnect("https://ticketsale.wimbledon.com/content", reconnect_time=4)
            
            logging.info(f"Successfully navigated to: {sb.get_current_url()}")
            
            # Test anti-detection
            logging.info("Testing anti-bot detection...")
            webdriver_detected = sb.execute_script("return navigator.webdriver")
            automation_detected = sb.execute_script("return window.chrome && window.chrome.runtime && window.chrome.runtime.onConnect")
            
            logging.info(f"navigator.webdriver: {webdriver_detected}")
            logging.info(f"Chrome automation detected: {automation_detected is not None}")
            
            if not webdriver_detected:
                logging.info("SUCCESS: navigator.webdriver is properly hidden.")
            else:
                logging.warning("WARNING: navigator.webdriver is exposed")
            
            # Check user agent
            user_agent = sb.execute_script("return navigator.userAgent")
            logging.info(f"User Agent: {user_agent}")
            
            # Wait to see the page
            logging.info("Keeping browser open for 100000 seconds to verify it works...")
            time.sleep(100000)
            
            logging.info("Test completed successfully.")
            
        except Exception as e:
            logging.error(f"Test failed: {e}")
            raise

if __name__ == "__main__":
    test_undetectable_chrome()


