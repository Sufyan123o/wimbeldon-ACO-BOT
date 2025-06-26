"""
Wimbledon Ticket Monitor & Automated Purchase Bot

This script monitors the Wimbledon ticket API for Centre Court ticket restocks
and automatically attempts to purchase tickets when they become available.

Features:
- Automated login with captcha solving using CapSolver
- Bypasses waiting room by auto-clicking "Enter" button
- Monitors API for Centre Court ticket availability changes
- Sends Discord webhook notifications with second-precision timestamps
- AUTOMATED PURCHASING: When tickets are detected, automatically:
  * Navigates to the buy page
  * Sets quantity to 2 tickets
  * Adds tickets to cart
- Real-time monitoring with random delays (0.1-0.8 seconds)

Required Environment Variables:
- LOGIN_EMAIL: Your Wimbledon account email
- LOGIN_PASSWORD: Your Wimbledon account password
- API_KEY: Your CapSolver API key for captcha solving
- DISCORD_WEBHOOK_URL: Discord webhook URL for notifications

Usage:
1. Set up your .env file with the required credentials
2. Run the script: python test.py
3. The bot will automatically log in, solve captchas, and start monitoring
4. When tickets are found, it will attempt to purchase them automatically
5. Check Discord for notifications and your cart for tickets!
"""

import requests
import random
import base64
import os
import urllib3
import time
import json
from datetime import datetime, UTC
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
            if driver.find_elements(By.ID, "actionButton") or driver.find_elements(
                By.ID, "loginID"
            ):
                bypassed = True
                print("✅ Captcha bypassed!")

                # Check if we're in a waiting room and need to click Enter button
                try:
                    enter_button = driver.find_element(By.ID, "actionButton")
                    if enter_button.is_displayed():
                        print(
                            "🕐 Waiting room detected! Waiting for Enter button to be clickable..."
                        )
                        # Wait up to 15 seconds for the button to become clickable
                        wait = WebDriverWait(driver, 15)
                        clickable_button = wait.until(
                            EC.element_to_be_clickable((By.ID, "actionButton"))
                        )
                        clickable_button.click()
                        print("✅ Enter button clicked! Proceeding to main site...")
                        time.sleep(2)  # Give it a moment to load
                except Exception as e:
                    print(f"No waiting room Enter button found or already past it: {e}")

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
        btn = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        )
        print("Accepting cookies...")
        btn.click()
    except:
        print("No cookie banner found, continuing...")

    # Wait for email/password fields to be visible
    print("Looking for login form...")
    wait = WebDriverWait(driver, 20)
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
        "maxPerformances": 50,
        "maxTimeslots": 50,
        "maxPerformanceDays": 3,
        "maxTimeslotDays": 3,
        "includeMetadata": "true",
    }

    print(f"\nMaking API request to: {api_url}")
    try:
        api_response = session.get(
            api_url, headers=headers, params=params, verify=False
        )
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
                print(
                    f"Response keys: {list(response_data.keys()) if isinstance(response_data, dict) else 'Not a dict'}"
                )
                if isinstance(response_data, dict) and len(response_data) > 0:
                    for key, value in list(response_data.items())[
                        :3
                    ]:  # Show first 3 keys
                        print(
                            f"  {key}: {str(value)[:100]}{'...' if len(str(value)) > 100 else ''}"
                        )
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
        "description": f"[**{day_name}** tickets are now available!]({buy_link})",
        "color": 0x00FF00,  # Green color
        "fields": [
            {"name": "Event", "value": day_name, "inline": True},
            {"name": "Advantage Type", "value": advantage_name, "inline": True},
            {
                "name": "Purchase Link",
                "value": f"[Buy Tickets]({buy_link})",
                "inline": False,
            },
        ],
        "footer": {
            "text": f"Wimbledon Ticket Monitor – {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

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


def automated_add_to_cart(driver, buy_link, day_name, start_time=None):
    """
    Navigate to buy link and automatically add 2 tickets to cart
    """
    try:
        if start_time is None:
            start_time = time.time()

        print(f"🛒 Starting automated purchase for {day_name}")
        print(f"   Navigating to: {buy_link}")

        # Navigate to the buy page
        navigation_start = time.time()
        driver.get(buy_link)
        navigation_time = time.time() - navigation_start
        print(f"   ⏱️ Page navigation took: {navigation_time:.2f} seconds")
        # Wait for page to load
        wait = WebDriverWait(driver, 10)  # Reduced from 15 to 10 seconds

        # Wait for the ticket selection table to be present
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))

        print("   Buy page loaded, looking for available tickets...")

        # Look for the first available quantity dropdown (not sold out)
        # From the HTML, quantity dropdowns are in td.quantity select elements
        quantity_selects = driver.find_elements(By.CSS_SELECTOR, "td.quantity select")

        ticket_added = False
        for select in quantity_selects:
            try:
                # Check if this select is visible and not in a sold out row
                if select.is_displayed():
                    # Check if parent row is not sold out
                    parent_row = select.find_element(By.XPATH, "./ancestor::tr[1]")
                    if "category_unavailable" not in parent_row.get_attribute("class"):
                        print(
                            "   Found available ticket category, setting quantity to 2..."
                        )
                        # Use JavaScript to set the value to 2 and trigger events immediately
                        driver.execute_script(
                            "arguments[0].value = '2'; arguments[0].dispatchEvent(new Event('change', { bubbles: true })); arguments[0].dispatchEvent(new Event('input', { bubbles: true }));",
                            select,
                        )

                        print("✅ Quantity set to 2")
                        ticket_added = True
                        break
            except Exception as e:
                print(f"   Skipping select element: {e}")
                continue

        if not ticket_added:
            print("❌ No available tickets found on the page")
            return False

        # Look for and click the "Add to cart" button
        cart_process_start = time.time()
        print("   Looking for 'Add to cart' button...")

        # The button is actually a link with id="book" inside #addToCartButtonContainer
        try:
            # Wait for the add to cart button to be present (reduced timeout for speed)
            WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, "book"))
            )

            # Try multiple selectors for the add to cart button
            add_to_cart_selectors = [
                "#book",  # Primary selector - the actual add to cart link
                "#addToCartButtonContainer a",  # Alternative selector
                "a[onclick*='validateQuantities']",  # Based on the onclick attribute
                ".stx_tfooter_buttons_container a",  # Container-based selector
            ]

            add_to_cart_button = None
            for selector in add_to_cart_selectors:
                try:
                    add_to_cart_button = driver.find_element(By.CSS_SELECTOR, selector)
                    if (
                        add_to_cart_button.is_displayed()
                        and add_to_cart_button.is_enabled()
                    ):
                        print(f"   Found add to cart button using selector: {selector}")
                        break
                except:
                    continue

            if add_to_cart_button:
                print("   Clicking 'Add to cart' button...")

                # Try JavaScript click first (more reliable for complex onclick handlers)
                try:
                    driver.execute_script("arguments[0].click();", add_to_cart_button)
                    print("✅ Add to cart button clicked via JavaScript!")
                except:
                    # Fallback to regular click                    add_to_cart_button.click()
                    print("✅ Add to cart button clicked via regular click!")

                # Wait a moment to see if we get redirected or see confirmation
                time.sleep(0.5)  # Reduced from 1 second to 0.5 seconds for speed

                # Check for error messages first (most important check)
                error_messages = driver.find_elements(
                    By.CSS_SELECTOR, ".message.error, .error"
                )
                has_errors = False

                for msg in error_messages:
                    if msg.is_displayed() and msg.text.strip():
                        error_text = msg.text.strip()
                        print(f"❌ Error message detected: {error_text}")
                        has_errors = True

                        # Check for specific error types
                        if "insufficient tickets" in error_text.lower():
                            print("   → Insufficient tickets available")
                            return False
                        elif "no longer available" in error_text.lower():
                            print("   → Tickets no longer available")
                            return False
                        elif "sold out" in error_text.lower():
                            print("   → Tickets sold out")
                            return False

                if has_errors:
                    print("❌ Purchase failed due to errors on page")
                    return False
                # Check if we were successful (look for cart page or confirmation)
                current_url = driver.current_url
                if "cart" in current_url.lower() or "basket" in current_url.lower():
                    cart_process_time = time.time() - cart_process_start
                    total_time = time.time() - start_time
                    print(
                        "🎉 Successfully added tickets to cart! (URL indicates cart page)"
                    )
                    print(
                        f"   ⏱️ Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
                    )
                    return True
                # Additional success indicators - look for cart-related elements
                success_indicators = [
                    ".cart",
                    ".basket",
                    "[class*='cart']",
                    "[id*='cart']",
                    ".success",
                    ".confirmation",
                    "[class*='success']",
                ]

                for indicator in success_indicators:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, indicator)
                        if elements and any(elem.is_displayed() for elem in elements):
                            cart_process_time = time.time() - cart_process_start
                            total_time = time.time() - start_time
                            print(f"🎉 Success indicator found: {indicator}")
                            print(
                                f"   ⏱️ Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
                            )
                            return True
                    except:
                        continue
                # Check if the total amount has changed (indicating tickets were added)
                try:
                    total_elements = driver.find_elements(
                        By.CSS_SELECTOR, ".reservation_amount, [data-amount], .amount"
                    )
                    for elem in total_elements:
                        if elem.is_displayed():
                            amount_text = elem.get_attribute("data-amount") or elem.text
                            if (
                                amount_text
                                and amount_text != "0"
                                and "£0" not in amount_text
                            ):
                                cart_process_time = time.time() - cart_process_start
                                total_time = time.time() - start_time
                                print(f"🎉 Non-zero amount detected: {amount_text}")
                                print(
                                    f"   ⏱️ Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
                                )
                                return True
                except:
                    pass

                # If no clear success indicators and no errors, it's uncertain
                print(f"⚠️ Uncertain result - Current URL: {current_url}")
                print(
                    "   No clear error messages, but no definitive success indicators either"
                )
                return False  # Changed from True to False - be conservative
            else:
                print("❌ Could not find 'Add to cart' button")
                print("   Checking page for debugging...")
                # Debug: check if the button container exists
                try:
                    container = driver.find_element(By.ID, "addToCartButtonContainer")
                    print(
                        f"   Button container found: {container.get_attribute('outerHTML')[:200]}..."
                    )
                except:
                    print("   Button container (#addToCartButtonContainer) not found")
                return False

        except Exception as e:
            print(f"❌ Error clicking submit button: {e}")
            return False

    except Exception as e:
        print(f"❌ Error during automated purchase: {e}")
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
        "description": "Test notification",
        "color": 0x0099FF,  # Blue color
        "fields": [
            {"name": "Status", "value": "✅ Webhook Connected", "inline": True},
            {"name": "Monitoring", "value": " ", "inline": True},
        ],
        "footer": {
            "text": f"Wimbledon Ticket Monitor – Test Message – {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print("✅ Test Discord notification sent successfully!")
            return True
        else:
            print(
                f"❌ Failed to send test Discord notification: {response.status_code}"
            )
            return False
    except Exception as e:
        print(f"❌ Error sending test Discord notification: {e}")
        return False


def send_success_notification(day_name, advantage_name, total_time=None):
    """
    Send a success notification to Discord after successful purchase
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        return False

    # Prepare timing field if available
    timing_field = []
    if total_time is not None:
        timing_field = [
            {
                "name": "⏱️ Speed",
                "value": f"{total_time:.3f} seconds from detection to cart",
                "inline": False,
            }
        ]

    embed = {
        "title": "✅ Added to Cart Successfully!",
        "description": f"Tickets successfully added to cart for **{day_name}**",
        "color": 0x00FF00,  # Green color
        "fields": [
            {"name": "Event", "value": day_name, "inline": True},
            {"name": "Advantage Type", "value": advantage_name, "inline": True},
            {
                "name": "Status",
                "value": "✅ Tickets confirmed in cart - proceed to checkout!",
                "inline": False,
            },
        ]
        + timing_field,
        "footer": {
            "text": f"Wimbledon Automation – SUCCESS – {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"content": "@here", "embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        return response.status_code == 204
    except:
        return False


def send_failure_notification(day_name, advantage_name, total_time=None):
    """
    Send a failure notification to Discord when purchase attempt fails
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        return False

    # Prepare timing field if available
    timing_field = []
    if total_time is not None:
        timing_field = [
            {
                "name": "⏱️ Time Spent",
                "value": f"{total_time:.3f} seconds attempting purchase",
                "inline": False,
            }
        ]

    embed = {
        "title": "❌ Purchase Attempt Failed",
        "description": f"Automated purchase failed for **{day_name}**",
        "color": 0xFF0000,  # Red color
        "fields": [
            {"name": "Event", "value": day_name, "inline": True},
            {"name": "Advantage Type", "value": advantage_name, "inline": True},
            {
                "name": "Reason",
                "value": "Likely insufficient tickets or sold out during purchase attempt",
                "inline": False,
            },
        ]
        + timing_field,
        "footer": {
            "text": f"Wimbledon Automation – {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        return response.status_code == 204
    except:
        return False


def monitor_performances(session, headers, known_states=None, driver=None, automation_enabled=True):
    """
    Monitor performances for availability changes in Centre Court events
    """
    if known_states is None:
        known_states = {}

    api_url = "https://ticketsale.wimbledon.com/tnwr/v1/catalog"
    params = {
        "maxPerformances": 50,
        "maxTimeslots": 50,
        "maxPerformanceDays": 3,
        "maxTimeslotDays": 3,
        "includeMetadata": "true",
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
        sections = data.get("sections", [])
        changes_found = False

        for section in sections:
            clusters = section.get("clusters", [])
            for cluster in clusters:
                items = cluster.get("items", [])
                for item in items:
                    product = item.get("product", {})
                    performances = product.get("performances", [])

                    for performance in performances:
                        name = performance.get("name", {}).get("en", "")

                        # Check if this is a Centre Court event
                        if "Centre Court Day" in name:
                            perf_id = performance.get("performanceId")
                            buy_link = performance.get("action", {}).get("buy", "")
                            advantages = performance.get("advantages", [])

                            # Check each advantage for availability changes
                            for advantage in advantages:
                                advantage_id = advantage.get("id")
                                advantage_name = advantage.get("name", {}).get("en", "")
                                current_availability = advantage.get(
                                    "availability", "NONE"
                                )
                                # Create unique key for this advantage
                                state_key = f"{perf_id}_{advantage_id}"

                                # Check if this is a new state or changed from NONE
                                previous_availability = known_states.get(
                                    state_key, "NONE"
                                )

                                if (
                                    previous_availability == "NONE"
                                    and current_availability != "NONE"
                                ):
                                    # Send Discord notification first
                                    send_discord_webhook(name, buy_link, advantage_name)
                                    # Start timing from the moment we detect a change
                                    restock_detected_time = time.time()

                                    print(f"🎉 RESTOCK!")
                                    print(f"   Event: {name}")
                                    print(f"   Advantage: {advantage_name}")
                                    print(
                                        f"   Status: {previous_availability} → {current_availability}"
                                    )
                                    print(f"   Buy Link: {buy_link}")

                                    # Attempt automated purchase only if automation is enabled
                                    if automation_enabled and driver:
                                        print("🚀 Starting automated purchase process...")
                                        purchase_success = automated_add_to_cart(
                                            driver,
                                            buy_link,
                                            name,
                                            restock_detected_time,
                                        )

                                        # Calculate total time taken
                                        total_time = time.time() - restock_detected_time

                                        if purchase_success:
                                            print(
                                                f"🎉 Automated purchase completed successfully!"
                                            )
                                            print(
                                                f"⏱️ Total time from detection to cart: {total_time:.3f} seconds"
                                            )
                                            # Send success notification with timing info
                                            send_success_notification(
                                                name, advantage_name, total_time
                                            )
                                            # Disable automation after successful purchase
                                            automation_enabled = False
                                            print("🛑 ATC DISABLED")
                                            print("📋 You can now manually proceed to checkout in the browser")
                                            print("🔍 Monitoring will continue for additional opportunities")
                                        else:
                                            print(
                                                f"❌ Automated purchase failed - tickets likely unavailable or sold out"
                                            )
                                            print(
                                                f"⏱️ Total time attempted: {total_time:.3f} seconds"
                                            )
                                            # Send failure notification with timing info
                                            send_failure_notification(
                                                name, advantage_name, total_time
                                            )
                                    elif not automation_enabled:
                                        print("⚠️ ATC is DISABLED - tickets detected but no action taken")
                                        print("📋 Manual intervention required if you want these tickets")
                                    elif not driver:
                                        print(
                                            "⚠️ WebDriver not available for automated purchase"
                                        )

                                    changes_found = True

                                # Update known state
                                known_states[state_key] = current_availability

        if not changes_found:
            print("   No availability changes detected.")

        return known_states, automation_enabled

    except Exception as e:
        print(f"❌ Error during monitoring: {e}")
        return known_states, automation_enabled


def start_monitoring(session, headers, driver=None):
    """
    Start the continuous monitoring loop with optional WebDriver for automated purchasing
    """
    print("\n🔍 Starting Wimbledon ticket monitoring...")
    print("Monitoring Centre Court events for availability changes...")
    if driver:
        print("🤖 Automated purchasing enabled!")
    else:
        print("📢 Notifications only (no automated purchasing)")
    print("Checking every 30 seconds. Press Ctrl+C to stop.\n")

    known_states = {}
    automation_enabled = True  # Flag to control automated purchasing

    try:
        while True:
            known_states, automation_enabled = monitor_performances(
                session, headers, known_states, driver, automation_enabled
            )
            # random sleep between 0.8 and 0.1 seconds
            time.sleep(random.uniform(0.1, 0.8))
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
        session.cookies.set(
            cookie["name"], cookie["value"], domain=cookie.get("domain")
        )

    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en",
        "referer": "https://ticketsale.wimbledon.com/secured/content",
        "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        "x-api-key": "344152a6-fa57-4e09-951f-96b8a38927d9",
        "X-Secutix-Host": "ticketsale.wimbledon.com",
    }

    # Try to extract CSRF token from page if available
    try:
        csrf_token = driver.execute_script(
            "return window.csrfToken || document.querySelector('meta[name=\"csrf-token\"]')?.content"
        )
        if csrf_token:
            headers["x-csrf-token"] = csrf_token
            print(f"Found CSRF token: {csrf_token[:20]}...")
    except:
        print("No CSRF token found, proceeding without it...")

    return session, headers


def setup_browser():
    """
    Configure and return Chrome driver
    """
    options = Options()
    print("Starting browser...")
    # Set headless to False so you can see the automated purchasing
    options.headless = False

    # Add some useful options for automation
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)

    # Execute script to remove webdriver property
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    return driver


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
        time.sleep(5)  # Wait for page to load after login
        session, headers = make_api_request(driver)

        if not session or not headers:
            print("❌ Failed to create session for monitoring")
            return
        # Step 5: Send test webhook and start monitoring
        print("\n" + "=" * 50)
        print("✅ Setup complete! Session established.")
        print("=" * 50)

        print("\nSending test Discord notification...")
        send_test_webhook()

        print("\nStarting monitoring automatically...")
        start_monitoring(session, headers, driver)

    except Exception as e:
        print(f"Error: {e}")

    finally:
        print("\nBrowser will remain open for 10 seconds...")
        time.sleep(10)
        driver.quit()  # Close the browser


if __name__ == "__main__":
    main()
