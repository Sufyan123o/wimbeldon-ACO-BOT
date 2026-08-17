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
import secrets
import logging
import urllib.error
import chromedriver_autoinstaller
from datetime import datetime, UTC
from dotenv import load_dotenv
from seleniumbase import SB
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()


class DataDomeBanError(Exception):
    """Raised when DataDome returns a 403 ban during monitoring."""
    pass


# Separate webhook for operational/logging messages (DD bans, startup, etc.)
DISCORD_LOG_WEBHOOK_URL = "https://discord.com/api/webhooks/1488554500301131808/hwIE41TbRdandH-fXEc4EYkSGUcDheRrkg2YZ7C0w9ClHZUBxtz4GkfutDkyzSPPr4_H"

def send_datadome_ban_webhook(proxy=None):
    """Send Discord notification when DataDome bans the session."""
    webhook_url = DISCORD_LOG_WEBHOOK_URL
    if not webhook_url:
        return
    proxy_info = f"{proxy['ip']}:{proxy['port']}" if proxy else "direct"
    payload = {
        "content": f" **DataDome Ban Detected!**\n"
                   f"Proxy: `{proxy_info}`\n"
                   f"Restarting with a new proxy..."
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
        logging.info(" DataDome ban webhook sent")
    except Exception:
        pass

# Your CapSolver API key
api_key = os.getenv("API_KEY")

# CapSolver-supported user agent (MUST match what the browser uses)
CAPSOLVER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def load_proxies():
    """Load proxies from PROXIES env var. Format: IP:PORT:USERNAME:PASSWORD, one per line."""
    raw = os.getenv("PROXIES", "").strip()
    if not raw:
        logging.info("No proxies configured (set PROXIES in .env)")
        return []
    proxies = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            proxies.append({"ip": ip, "port": port, "user": user, "pass": pwd})
        elif len(parts) == 2:
            ip, port = parts
            proxies.append({"ip": ip, "port": port, "user": None, "pass": None})
        else:
            logging.warning(f"Skipping invalid proxy line: {line}")
    logging.info(f"Loaded {len(proxies)} proxies")
    return proxies


def pick_proxy(proxy_list):
    """Pick a random proxy from the list and return it."""
    if not proxy_list:
        return None
    return secrets.choice(proxy_list)


def get_proxy_string(proxy):
    """Convert proxy dict to CapSolver proxy format: IP:PORT:USER:PASS"""
    if not proxy:
        return None
    if proxy["user"] and proxy["pass"]:
        return f"{proxy['ip']}:{proxy['port']}:{proxy['user']}:{proxy['pass']}"
    return f"{proxy['ip']}:{proxy['port']}"


def solve_datadome(captcha_url, proxy, website_url="https://ticketsale.wimbledon.com/content"):
    """
    Solve DataDome slider/interstitial captcha using CapSolver.
    Returns the datadome cookie string, or None on failure.
    """
    proxy_str = get_proxy_string(proxy)
    if not proxy_str:
        logging.error(" DataDome solver requires a proxy!")
        return None

    # Check if IP is banned (t=bv means banned, t=fe means solvable)
    if "t=bv" in captcha_url:
        logging.error(" DataDome: IP is banned (t=bv in URL). Change your proxy!")
        return None

    logging.info(" Solving DataDome captcha with CapSolver...")
    logging.info(f"   captchaUrl: {captcha_url[:120]}...")

    # Step 1: Create task
    payload = {
        "clientKey": api_key,
        "task": {
            "type": "DatadomeSliderTask",
            "websiteURL": website_url,
            "captchaUrl": captcha_url,
            "userAgent": CAPSOLVER_USER_AGENT,
            "proxy": proxy_str,
        }
    }

    try:
        res = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
        resp = res.json()
    except Exception as e:
        logging.error(f" DataDome createTask request failed: {e}")
        return None

    task_id = resp.get("taskId")
    if not task_id:
        logging.error(f" DataDome createTask failed: {resp}")
        return None

    logging.info(f"   TaskId: {task_id} - polling for result...")

    # Step 2: Poll for result
    for attempt in range(60):  # max ~60 seconds
        time.sleep(1)
        try:
            poll_res = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=30
            )
            poll_resp = poll_res.json()
        except Exception as e:
            logging.warning(f"   Poll error: {e}")
            continue

        status = poll_resp.get("status")
        if status == "ready":
            cookie = poll_resp.get("solution", {}).get("cookie")
            if cookie:
                logging.info(f" DataDome solved! Cookie: {cookie[:60]}...")
                return cookie
            else:
                logging.error(f" DataDome solution missing cookie: {poll_resp}")
                return None
        elif status == "failed" or poll_resp.get("errorId"):
            logging.error(f" DataDome solve failed: {poll_resp}")
            return None
        # else: still processing

    logging.error(" DataDome solve timed out after 60 seconds")
    return None


def detect_and_solve_datadome(sb, proxy):
    """
    Check if DataDome captcha is present in the browser and solve it.
    Returns True if solved (or no DD present), False if failed.
    """
    try:
        # Check for DataDome iframe or redirect
        current_url = sb.get_current_url()

        # Method 1: We got redirected to geo.captcha-delivery.com
        if "geo.captcha-delivery.com" in current_url:
            logging.info(" DataDome captcha detected (direct redirect)")
            cookie = solve_datadome(current_url, proxy, website_url="https://ticketsale.wimbledon.com/content")
            if cookie:
                return _apply_datadome_cookie(sb, cookie)
            return False

        # Method 2: DataDome iframe embedded in page
        captcha_url = sb.execute_script("""
            // Check for DataDome iframe
            var iframes = document.querySelectorAll('iframe[src*="captcha-delivery"], iframe[src*="datadome"]');
            if (iframes.length > 0) return iframes[0].src;
            // Check for DataDome challenge div
            var ddEl = document.querySelector('#datadome, .datadome, [data-datadome]');
            if (ddEl) {
                var iframe = ddEl.querySelector('iframe');
                if (iframe) return iframe.src;
            }
            return null;
        """)

        if captcha_url:
            logging.info(f" DataDome iframe detected: {captcha_url[:100]}...")
            cookie = solve_datadome(captcha_url, proxy, website_url="https://ticketsale.wimbledon.com/content")
            if cookie:
                return _apply_datadome_cookie(sb, cookie)
            return False

        # Method 3: Check page content for DataDome block text
        page_source = sb.get_page_source()
        if "Access is temporarily restricted" in page_source or "captcha-delivery.com" in page_source:
            logging.info(" DataDome block page detected, extracting captcha URL...")
            # Try to find the captcha URL in the page source
            import re
            match = re.search(r'(https://geo\.captcha-delivery\.com/captcha/[^"\'>\s]+)', page_source)
            if match:
                captcha_url = match.group(1)
                logging.info(f"   Extracted captcha URL: {captcha_url[:100]}...")
                cookie = solve_datadome(captcha_url, proxy, website_url="https://ticketsale.wimbledon.com/content")
                if cookie:
                    return _apply_datadome_cookie(sb, cookie)
            else:
                logging.error(" Could not extract DataDome captcha URL from page")
            return False

        # No DataDome detected
        return True

    except Exception as e:
        logging.error(f" Error detecting DataDome: {e}")
        return True  # Don't block if detection fails


def _apply_datadome_cookie(sb, cookie_str):
    """
    Apply the solved DataDome cookie to the browser and reload.
    cookie_str format: "datadome=VALUE; Max-Age=...; Domain=...; Path=/; Secure; SameSite=Lax"
    """
    try:
        # Extract just the cookie value
        cookie_value = cookie_str.split("datadome=")[1].split(";")[0]

        # Set the cookie in the browser
        sb.execute_script(f"document.cookie = 'datadome={cookie_value}; path=/; secure; samesite=lax';")

        # Also add via Selenium's cookie API for the current domain
        sb.driver.add_cookie({
            "name": "datadome",
            "value": cookie_value,
            "domain": ".wimbledon.com",
            "path": "/",
            "secure": True,
            "sameSite": "Lax"
        })

        logging.info(" DataDome cookie applied, reloading page...")
        sb.driver.refresh()
        time.sleep(3)

        # Verify we're past the block
        new_url = sb.get_current_url()
        new_source = sb.get_page_source()
        if "geo.captcha-delivery.com" in new_url or "Access is temporarily restricted" in new_source:
            logging.error(" DataDome cookie didn't work - still blocked")
            return False

        logging.info(" DataDome bypass successful!")
        return True

    except Exception as e:
        logging.error(f" Error applying DataDome cookie: {e}")
        return False


def solve_recaptcha_v2(website_url, website_key, proxy=None):
    """
    Solve reCAPTCHA v2 using CapSolver's token approach.
    Returns the gRecaptchaResponse token, or None on failure.
    """
    logging.info(" Solving reCAPTCHA v2 with CapSolver...")
    logging.info(f"   websiteURL: {website_url}")
    logging.info(f"   websiteKey: {website_key}")

    task = {
        "type": "ReCaptchaV2TaskProxyLess",
        "websiteURL": website_url,
        "websiteKey": website_key,
    }

    # If proxy available, use the proxy variant for better accuracy
    if proxy:
        proxy_str = get_proxy_string(proxy)
        if proxy_str:
            task["type"] = "ReCaptchaV2Task"
            task["proxy"] = f"http:{proxy['ip']}:{proxy['port']}:{proxy['user']}:{proxy['pass']}"

    payload = {
        "clientKey": api_key,
        "task": task
    }

    try:
        res = requests.post("https://api.capsolver.com/createTask", json=payload, timeout=30)
        resp = res.json()
    except Exception as e:
        logging.error(f" reCAPTCHA createTask failed: {e}")
        return None

    task_id = resp.get("taskId")
    if not task_id:
        logging.error(f" reCAPTCHA createTask error: {resp}")
        return None

    logging.info(f"   TaskId: {task_id} - polling for result...")

    for _ in range(120):  # max ~120 seconds
        time.sleep(1)
        try:
            poll_res = requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=30
            )
            poll_resp = poll_res.json()
        except Exception as e:
            logging.warning(f"   Poll error: {e}")
            continue

        status = poll_resp.get("status")
        if status == "ready":
            token = poll_resp.get("solution", {}).get("gRecaptchaResponse")
            if token:
                logging.info(f" reCAPTCHA solved! Token: {token[:50]}...")
                return token
            else:
                logging.error(f" reCAPTCHA solution missing token: {poll_resp}")
                return None
        elif status == "failed" or poll_resp.get("errorId"):
            logging.error(f" reCAPTCHA solve failed: {poll_resp}")
            return None

    logging.error(" reCAPTCHA solve timed out after 120 seconds")
    return None


def detect_and_solve_recaptcha(sb, proxy=None):
    """
    Detect reCAPTCHA on the current page, solve it, and inject the token.
    Returns True if solved (or no reCAPTCHA), False if failed.
    """
    try:
        # Check for reCAPTCHA presence
        recaptcha_info = sb.execute_script("""
            // Check for reCAPTCHA iframe
            var recaptchaFrame = document.querySelector('iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"]');
            // Check for g-recaptcha div with sitekey
            var recaptchaDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
            // Check for grecaptcha object
            var hasGrecaptcha = typeof grecaptcha !== 'undefined';
            
            var sitekey = null;
            if (recaptchaDiv) {
                sitekey = recaptchaDiv.getAttribute('data-sitekey');
            }
            if (!sitekey && recaptchaFrame) {
                var src = recaptchaFrame.src;
                var match = src.match(/[?&]k=([^&]+)/);
                if (match) sitekey = match[1];
            }
            
            return {
                found: !!(recaptchaFrame || recaptchaDiv || hasGrecaptcha),
                sitekey: sitekey
            };
        """)

        if not recaptcha_info or not recaptcha_info.get("found"):
            return True  # No reCAPTCHA present

        sitekey = recaptcha_info.get("sitekey")
        if not sitekey:
            logging.error(" reCAPTCHA detected but could not extract sitekey")
            return False

        current_url = sb.get_current_url()
        logging.info(f" reCAPTCHA detected on page! Sitekey: {sitekey}")

        # Solve it
        token = solve_recaptcha_v2(current_url, sitekey, proxy)
        if not token:
            return False

        # Inject the token into the page
        inject_result = sb.execute_script("""
            var token = arguments[0];
            var callbackFired = false;
            
            // 1. Fill ALL g-recaptcha-response textareas
            var textareas = document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response, textarea.g-recaptcha-response');
            textareas.forEach(function(ta) {
                ta.innerHTML = token;
                ta.value = token;
            });
            
            // Also check inside iframes for textareas
            try {
                var recaptchaDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
                if (recaptchaDiv) {
                    var hiddenTextarea = recaptchaDiv.querySelector('textarea');
                    if (hiddenTextarea) {
                        hiddenTextarea.innerHTML = token;
                        hiddenTextarea.value = token;
                    }
                }
            } catch(e) {}
            
            // 2. Try data-callback attribute on .g-recaptcha div
            try {
                var gcDiv = document.querySelector('.g-recaptcha[data-callback], [data-sitekey][data-callback]');
                if (gcDiv) {
                    var cbName = gcDiv.getAttribute('data-callback');
                    if (cbName && typeof window[cbName] === 'function') {
                        window[cbName](token);
                        callbackFired = true;
                    }
                }
            } catch(e) {}
            
            // 3. Traverse ___grecaptcha_cfg.clients to find callbacks
            if (!callbackFired && typeof ___grecaptcha_cfg !== 'undefined') {
                try {
                    var clients = ___grecaptcha_cfg.clients;
                    if (clients) {
                        Object.keys(clients).forEach(function(cKey) {
                            var client = clients[cKey];
                            // Deep traverse all properties looking for callback functions
                            function findAndCall(obj, depth) {
                                if (depth > 8 || !obj || typeof obj !== 'object') return;
                                Object.keys(obj).forEach(function(k) {
                                    try {
                                        if (typeof obj[k] === 'function') {
                                            // Common callback property names
                                            if (k === 'callback' || k === 'success-callback' || k === 'success') {
                                                obj[k](token);
                                                callbackFired = true;
                                            }
                                        } else if (typeof obj[k] === 'object' && obj[k] !== null) {
                                            findAndCall(obj[k], depth + 1);
                                        }
                                    } catch(e) {}
                                });
                            }
                            findAndCall(client, 0);
                        });
                    }
                } catch(e) {}
            }
            
            // 4. Try grecaptcha.getResponse replacement approach
            if (!callbackFired) {
                try {
                    if (typeof grecaptcha !== 'undefined') {
                        // Try to find widget ID and use enterprise or standard callback
                        var widgetId = 0;
                        try {
                            // Find all rendered widgets
                            if (___grecaptcha_cfg && ___grecaptcha_cfg.clients) {
                                var keys = Object.keys(___grecaptcha_cfg.clients);
                                if (keys.length > 0) widgetId = parseInt(keys[0]);
                            }
                        } catch(e) {}
                        
                        // Override getResponse to return our token
                        var origGetResponse = grecaptcha.getResponse;
                        grecaptcha.getResponse = function(id) { return token; };
                        
                        // Try enterprise variant
                        if (grecaptcha.enterprise) {
                            var origEntGetResponse = grecaptcha.enterprise.getResponse;
                            grecaptcha.enterprise.getResponse = function(id) { return token; };
                        }
                    }
                } catch(e) {}
            }
            
            return {callbackFired: callbackFired, textareasFound: textareas.length};
        """, token)

        logging.info(f" reCAPTCHA token injected! Callback fired: {inject_result.get('callbackFired')}, Textareas: {inject_result.get('textareasFound')}")
        
        # If callback didn't fire, try clicking the reCAPTCHA checkbox as a fallback
        if not inject_result.get('callbackFired'):
            logging.info(" Callback not found, trying to trigger form validation...")
            try:
                # Try to find and click the recaptcha checkbox iframe
                sb.execute_script("""
                    var token = arguments[0];
                    // Try to find the callback by looking at the form's onsubmit or submit button onclick
                    var forms = document.querySelectorAll('form');
                    forms.forEach(function(form) {
                        var textareas = form.querySelectorAll('textarea');
                        textareas.forEach(function(ta) {
                            if (ta.id && ta.id.indexOf('g-recaptcha-response') !== -1) {
                                ta.value = token;
                                ta.innerHTML = token;
                            }
                        });
                    });
                    
                    // Dispatch change event on textarea
                    var mainTa = document.getElementById('g-recaptcha-response');
                    if (mainTa) {
                        mainTa.dispatchEvent(new Event('change', {bubbles: true}));
                        mainTa.dispatchEvent(new Event('input', {bubbles: true}));
                    }
                """, token)
            except Exception as e:
                logging.warning(f"Fallback callback trigger failed: {e}")
        
        time.sleep(1)
        return True

    except Exception as e:
        logging.error(f" Error detecting/solving reCAPTCHA: {e}")
        return False

class UserAgent:
    """Dynamic user agent management with real-time scraping"""
    def __init__(self):
        # Fallback agents if scraping fails
        self.base_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ]
    
    def get_user_agents(self):
        """Get fresh user agents from the web"""
        agent = secrets.choice(self.base_agents)
        headers = {"User-Agent": agent}
        
        try:
            response = requests.get("https://www.useragents.me", headers=headers, timeout=5)
            soup = BeautifulSoup(response.content, "html.parser")
            agents = soup.find_all("textarea", class_="form-control")
            
            if agents:
                fresh_agents = [
                    agent.string.strip() 
                    for agent in agents[:25] 
                    if agent.string
                ]
                logging.info(f" Loaded {len(fresh_agents)} fresh user agents")
                return fresh_agents
        except Exception as e:
            logging.warning(f"Failed to get fresh user agents: {e}")
        
        logging.info(" Using fallback user agents")
        return self.base_agents

class RequestHandler:
    """Smart HTTP request handler with user agent rotation"""
    def __init__(self):
        self.user_agents = UserAgent()
        self.agent_list = self.user_agents.get_user_agents()
    
    def send_request(self, url, payload=None):
        """Try regular HTTP first - much faster and less detectable"""
        from urllib.parse import urlencode
        
        if payload:
            query_string = urlencode(payload, True)
            url = f"{url}?{query_string}"
        
        session = requests.Session()
        session.headers.update({
            "User-Agent": secrets.choice(self.agent_list),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
        
        try:
            response = session.get(url, timeout=10)
            return response
        except Exception as e:
            logging.warning(f"Regular request failed: {e}")
            return None

class UndetectableBrowser:
    """ The Crown Jewel - Undetectable Chrome with SeleniumBase"""
    def __init__(self):
        self.request_handler = RequestHandler()
        self.user_agents = UserAgent()
        
    def setup_browser(self):
        """Configure undetectable Chrome browser"""
        try:
            # Auto-install matching chromedriver
            logging.info(" Installing/updating ChromeDriver...")
            chromedriver_autoinstaller.install()
        except urllib.error.URLError as e:
            logging.error(f"ChromeDriver installation failed: {e}")
            raise
    
    def create_undetectable_browser(self):
        """Create undetectable browser instance"""
        self.setup_browser()
        
        # MUST use CapSolver-supported user agent for DataDome solving
        selected_agent = CAPSOLVER_USER_AGENT
        logging.info(f" Using CapSolver-compatible user agent: {selected_agent[:80]}...")
        
        #  THE MAGIC HAPPENS HERE 
        browser_config = {
            'uc': True,  #  UNDETECTED CHROME - This is the secret sauce!
            'headless': False,  # Keep visible for login/captcha solving
            'incognito': False,  # Use regular mode to preserve session
            'user_data_dir': None,  # Let SeleniumBase handle this
            'agent': selected_agent,  # Use CapSolver-supported agent
            'chromium_arg': '--webrtc-ip-handling-policy=disable_non_proxied_udp,--disable-webrtc-hw-decoding,--disable-webrtc-hw-encoding,--disable-features=WebRtcHideLocalIpsWithMdns',
        }
        
        return browser_config
    
    def apply_proxy(self, browser_config, proxy):
        """Add proxy settings to the browser config."""
        if proxy is None:
            return browser_config
        if proxy["user"] and proxy["pass"]:
            # Let the extension handle BOTH proxy routing AND auth.
            # Do NOT set browser_config['proxy'] - it conflicts with the extension.
            ext_dir = self._create_proxy_auth_extension(proxy)
            browser_config['extension_dir'] = ext_dir
        else:
            browser_config['proxy'] = f"{proxy['ip']}:{proxy['port']}"
        logging.info(f" Proxy set: {proxy['ip']}:{proxy['port']}")
        return browser_config

    @staticmethod
    def _create_proxy_auth_extension(proxy):
        """Create a Chrome extension that auto-fills proxy auth credentials."""
        import tempfile
        ext_dir = os.path.join(tempfile.gettempdir(), "proxy_auth_ext")
        os.makedirs(ext_dir, exist_ok=True)

        manifest = {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Proxy Auth",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking",
                "privacy"
            ],
            "background": {"scripts": ["background.js"]},
            "minimum_chrome_version": "22.0.0"
        }

        background_js = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "http",
            host: "%s",
            port: parseInt(%s)
        },
        bypassList: ["localhost"]
    }
};

chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

function callbackFn(details) {
    return {
        authCredentials: {
            username: "%s",
            password: "%s"
        }
    };
}

chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {urls: ["<all_urls>"]},
    ['blocking']
);

// Block WebRTC IP leak - runs after proxy is set up
try {
    if (chrome.privacy && chrome.privacy.network) {
        chrome.privacy.network.webRTCIPHandlingPolicy.set({
            value: 'disable_non_proxied_udp'
        });
    }
} catch(e) {}
""" % (proxy["ip"], proxy["port"], proxy["user"], proxy["pass"])

        with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(ext_dir, "background.js"), "w") as f:
            f.write(background_js)

        logging.info(f" Proxy auth extension created at {ext_dir}")
        return ext_dir
        
    def smart_navigate(self, sb, url, proxy=None, wait_time=5, max_dd_retries=3):
        """Ultra-conservative navigation with DataDome auto-solving"""
        try:
            # Simple, natural navigation
            sb.uc_open_with_reconnect(url, reconnect_time=4)
            
            # Natural human-like delay
            time.sleep(wait_time)
            
            # Check for DataDome and solve if present
            for dd_attempt in range(max_dd_retries):
                if detect_and_solve_datadome(sb, proxy):
                    break
                else:
                    logging.warning(f" DataDome solve attempt {dd_attempt + 1}/{max_dd_retries} failed")
                    if dd_attempt < max_dd_retries - 1:
                        logging.info("   Retrying...")
                        time.sleep(2)
                    else:
                        logging.error(" All DataDome solve attempts failed")
                        return False
            
            logging.info(f" Successfully navigated to: {url}")
            return True
            
        except Exception as e:
            logging.error(f"Navigation failed: {e}")
            return False


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
    
    # Wait up to 10 seconds for the captcha image to be present
    print("Waiting for captcha image to load...")
    try:
        wait = WebDriverWait(driver, 10)
        img_elem = wait.until(EC.presence_of_element_located((By.ID, "img_captcha")))
        print(" Captcha image found!")
    except Exception as e:
        print(f" Captcha image not found after 10 seconds: {e}")
        return False
    
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
                print(" Captcha bypassed!")

                # Check if we're in a waiting room and need to click Enter button
                try:
                    enter_button = driver.find_element(By.ID, "actionButton")
                    if enter_button.is_displayed():
                        print(
                            " Waiting room detected! Waiting for Enter button to be clickable..."
                        )
                        # Wait up to 15 seconds for the button to become clickable
                        wait = WebDriverWait(driver, 15)
                        clickable_button = wait.until(
                            EC.element_to_be_clickable((By.ID, "actionButton"))
                        )
                        clickable_button.click()
                        print(" Enter button clicked! Proceeding to main site...")
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

            print(" Waiting for login to complete and session to initialize...")
            print("   (This includes time for any manual reCAPTCHA solving if needed)")
            time.sleep(15)  # Extended wait for session initialization and potential reCAPTCHA
            print(" Login wait period completed")
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


#  SeleniumBase versions of functions (DataDome-safe)
def bypass_captcha_sb(sb, proxy=None):
    """
    SeleniumBase version: Handle the captcha solving process
    """
    attempts = 0
    
    # Wait up to 10 seconds for the captcha image to be present
    logging.info("Waiting for captcha image to load...")
    try:
        sb.wait_for_element_present("#img_captcha", timeout=10)
        logging.info(" Captcha image found!")
    except Exception as e:
        logging.error(f" Captcha image not found after 10 seconds: {e}")
        return False
    
    # Take screenshot of captcha image
    try:
        # Get screenshot of just the captcha element (more accurate than full page)
        captcha_element = sb.find_element("#img_captcha")
        captcha_screenshot = captcha_element.screenshot_as_png
        b64_data = base64.b64encode(captcha_screenshot).decode("utf-8")
        logging.info(" Captcha element screenshot captured")
    except Exception as e:
        logging.error(f" Error taking captcha screenshot: {e}")
        return False

    logging.info(" Solving captcha with CapSolver...")
    result = solve_captcha(b64_data)

    if not result:
        logging.error(" Failed to solve captcha")
        return False

    bypassed = False
    while not bypassed and attempts < 3:  # Limit attempts to prevent infinite loop
        logging.info(f" Captcha attempt {attempts + 1}: {result}")
        
        try:
            # Clear and enter captcha solution
            sb.clear("#secret")
            sb.type("#secret", str(result))
            sb.execute_script("submitCaptcha();")
            
            # Wait a moment for page to respond
            time.sleep(2)
            
            # Check if DataDome appeared (means captcha was correct, DD is next challenge)
            if detect_and_solve_datadome(sb, proxy=proxy):
                logging.info(" DataDome solved after captcha submission")
                time.sleep(2)
            
            # Check if we're past the captcha (look for next page elements)
            if not sb.is_element_present("#img_captcha") or sb.is_element_present("#actionButton") or sb.is_element_present("#loginID"):
                bypassed = True
                logging.info(" Captcha bypassed!")

                # Check if we're in a waiting room and need to click Enter button
                try:
                    if sb.is_element_visible("#actionButton"):
                        logging.info(" Waiting room detected! Clicking Enter button...")
                        sb.wait_for_element_clickable("#actionButton", timeout=15)
                        sb.click("#actionButton")
                        logging.info(" Enter button clicked! Proceeding to main site...")
                        time.sleep(2)  # Give it a moment to load
                except Exception as e:
                    logging.info(f"No waiting room Enter button found or already past it: {e}")
                    
        except Exception as e:
            logging.error(f" Error during captcha submission: {e}")

        if not bypassed:
            attempts += 1
            if attempts < 3:
                logging.info(" Captcha failed, retrying...")
                # Get new captcha image
                try:
                    captcha_element = sb.find_element("#img_captcha")
                    captcha_screenshot = captcha_element.screenshot_as_png
                    b64_data = base64.b64encode(captcha_screenshot).decode("utf-8")
                    result = solve_captcha(b64_data)
                    if not result:
                        break
                except Exception as e:
                    logging.error(f" Error getting new captcha: {e}")
                    break

    return bypassed


def handle_login_sb(sb, proxy=None):
    """
    SeleniumBase version: Handle the login process after captcha
    """
    logging.info(" Waiting for page to load...")

    # Accept cookies if they're still up
    try:
        accept_btn = None
        for selector in ["button:contains('Accept All Cookies')", "#onetrust-accept-btn-handler", "button:contains('Accept')"]:
            try:
                if sb.is_element_present(selector, timeout=3):
                    accept_btn = selector
                    break
            except Exception:
                continue
        if accept_btn:
            logging.info(" Accepting cookies...")
            sb.click(accept_btn)
            time.sleep(1)
    except:
        logging.info("No cookie banner found, continuing...")

    # Wait for email/password fields to be visible
    logging.info(" Looking for login form...")
    try:
        sb.wait_for_element_visible("#loginID", timeout=20)
        
        if sb.is_element_visible("#loginID") and sb.is_element_visible("#password"):
            logging.info(" Login form found, entering credentials...")

            # Fill in credentials
            email = os.getenv("LOGIN_EMAIL")
            password = os.getenv("LOGIN_PASSWORD")

            if email and password:
                sb.clear("#loginID")
                sb.type("#loginID", email)

                sb.clear("#password")
                sb.type("#password", password)
                logging.info(" Login credentials entered!")

                # Step 1: Press login until reCAPTCHA/captcha message appears
                logging.info(" Pressing login to trigger reCAPTCHA requirement...")
                captcha_required = False
                login_already_succeeded = False
                for login_attempt in range(3):
                    # Try pressing Enter on password field - if it's gone, login already succeeded
                    try:
                        if not sb.is_element_present("#password"):
                            logging.info(" Login form gone - login already succeeded!")
                            login_already_succeeded = True
                            break
                        sb.press_keys("#password", "\n")
                    except Exception as press_err:
                        logging.info(f" Password field vanished (login succeeded): {press_err}")
                        login_already_succeeded = True
                        break
                    
                    logging.info(f" Login press {login_attempt + 1}...")
                    
                    # Wait and repeatedly check if login form disappeared
                    for _wait in range(10):
                        time.sleep(1)
                        try:
                            if not sb.is_element_present("#password"):
                                logging.info(" Login succeeded - password field gone!")
                                login_already_succeeded = True
                                break
                            current_url = sb.get_current_url()
                            if "login" not in current_url.lower() and "mywimbledon" not in current_url.lower():
                                logging.info(f" Login succeeded - navigated to: {current_url}")
                                login_already_succeeded = True
                                break
                        except Exception:
                            logging.info(" Login succeeded - page context changed!")
                            login_already_succeeded = True
                            break
                    
                    if login_already_succeeded:
                        break
                    
                    # Check if "captcha" text appeared in any error message
                    page_text = sb.execute_script("""
                        var errors = document.querySelectorAll('.gigya-error-msg, .gigya-error-msg-active, [data-bound-to]');
                        var text = '';
                        errors.forEach(function(e) { text += ' ' + e.textContent; });
                        return text.toLowerCase();
                    """) or ""
                    
                    if 'captcha' in page_text or 'recaptcha' in page_text:
                        logging.info(" Captcha requirement triggered!")
                        captcha_required = True
                        break
                    logging.info(f"   Error text: {page_text.strip()[:100] if page_text.strip() else 'none'}")

                if login_already_succeeded:
                    logging.info(" Waiting for session to initialize...")
                    time.sleep(10)
                    logging.info(" Login wait period completed")
                    return True

                if not captcha_required:
                    logging.warning(" Captcha requirement not detected, proceeding anyway...")

                # Step 2: Now detect and solve the reCAPTCHA that appeared
                recaptcha_info = sb.execute_script("""
                    var recaptchaDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
                    var sitekey = recaptchaDiv ? recaptchaDiv.getAttribute('data-sitekey') : null;
                    if (!sitekey) {
                        var frame = document.querySelector('iframe[src*="recaptcha"]');
                        if (frame) {
                            var match = frame.src.match(/[?&]k=([^&]+)/);
                            if (match) sitekey = match[1];
                        }
                    }
                    return {found: !!sitekey, sitekey: sitekey};
                """)

                recaptcha_token = None
                if recaptcha_info and recaptcha_info.get("found"):
                    sitekey = recaptcha_info.get("sitekey")
                    current_url = sb.get_current_url()
                    logging.info(f" reCAPTCHA appeared! Sitekey: {sitekey}")

                    for attempt in range(3):
                        recaptcha_token = solve_recaptcha_v2(current_url, sitekey, proxy)
                        if recaptcha_token:
                            break
                        logging.warning(f" reCAPTCHA solve attempt {attempt + 1}/3 failed")
                        if attempt < 2:
                            time.sleep(2)
                else:
                    logging.info(" No reCAPTCHA detected, login may have succeeded already")

                # Step 3: If we got a token, inject it and press Enter again
                if recaptcha_token:
                    logging.info(" reCAPTCHA solved! Injecting token...")

                    sb.execute_script("""
                        var token = arguments[0];
                        // Fill all g-recaptcha-response textareas
                        var textareas = document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response');
                        textareas.forEach(function(ta) {
                            ta.innerHTML = token;
                            ta.value = token;
                        });
                        // Also check inside .g-recaptcha div
                        var gcDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
                        if (gcDiv) {
                            var ta = gcDiv.querySelector('textarea');
                            if (ta) { ta.innerHTML = token; ta.value = token; }
                        }
                        
                        // Fire the callback
                        // 1. data-callback attribute
                        if (gcDiv) {
                            var cbName = gcDiv.getAttribute('data-callback');
                            if (cbName && typeof window[cbName] === 'function') {
                                window[cbName](token);
                            }
                        }
                        // 2. ___grecaptcha_cfg clients
                        if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                            Object.keys(___grecaptcha_cfg.clients).forEach(function(cKey) {
                                var client = ___grecaptcha_cfg.clients[cKey];
                                function findCb(obj, depth) {
                                    if (depth > 8 || !obj || typeof obj !== 'object') return;
                                    Object.keys(obj).forEach(function(k) {
                                        try {
                                            if (typeof obj[k] === 'function' && (k === 'callback' || k === 'success-callback' || k === 'success')) {
                                                obj[k](token);
                                            } else if (typeof obj[k] === 'object' && obj[k] !== null) {
                                                findCb(obj[k], depth + 1);
                                            }
                                        } catch(e) {}
                                    });
                                }
                                findCb(client, 0);
                            });
                        }
                        // 3. Override getResponse
                        if (typeof grecaptcha !== 'undefined') {
                            grecaptcha.getResponse = function() { return token; };
                            if (grecaptcha.enterprise) {
                                grecaptcha.enterprise.getResponse = function() { return token; };
                            }
                        }
                    """, recaptcha_token)

                    logging.info(" Token injected and callback fired, pressing Enter...")
                    time.sleep(1)
                    sb.press_keys("#password", "\n")
                    logging.info(" Login form submitted!")

                logging.info(" Waiting for login to complete and session to initialize...")
                time.sleep(10)
                logging.info(" Login wait period completed")
                return True
            else:
                logging.error(" No email/password found in environment variables!")
                logging.error("Please set LOGIN_EMAIL and LOGIN_PASSWORD in your .env file")
                return False
        else:
            logging.info(" Login form fields not visible, continuing...")
            return True
            
    except Exception as e:
        logging.error(f" Error during login process: {e}")
        return False


def save_page_html_sb(sb):
    """
    SeleniumBase version: Save the current page HTML to file
    """
    logging.info(" Getting final page HTML...")
    try:
        result_html = sb.get_page_source()

        with open("result_page.html", "w", encoding="utf-8") as f:
            f.write(result_html)
        logging.info(" Result page saved to 'result_page.html'")
        return True
    except Exception as e:
        logging.error(f" Error saving page HTML: {e}")
        return False


def create_session_from_sb(sb):
    """
    SeleniumBase version: Extract cookies and create requests session
    """
    logging.info(" Extracting session cookies for API requests...")
    
    try:
        # Get current URL to ensure we're on the right page
        current_url = sb.get_current_url()
        logging.info(f" Current page URL: {current_url}")
        
        if "content" not in current_url:
            logging.info(" Not on content page, navigating there first...")
            sb.open("https://ticketsale.wimbledon.com/content")
            time.sleep(3)
        
        # Extract cookies using SeleniumBase
        cookies = sb.driver.get_cookies()
        
        # Create requests session
        session = requests.Session()
        
        # Apply proxy to requests session if configured
        proxy_list = load_proxies()
        proxy = pick_proxy(proxy_list)
        if proxy:
            if proxy["user"] and proxy["pass"]:
                proxy_url = f"http://{proxy['user']}:{proxy['pass']}@{proxy['ip']}:{proxy['port']}"
            else:
                proxy_url = f"http://{proxy['ip']}:{proxy['port']}"
            session.proxies = {"http": proxy_url, "https": proxy_url}
            logging.info(f" Requests session using proxy: {proxy['ip']}:{proxy['port']}")
        
        # Add cookies to session
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])
        
        # Get user agent from browser
        user_agent = sb.execute_script("return navigator.userAgent;")
        
        # Extract x-api-key and x-csrf-token from the page
        x_api_key = ""
        x_csrf_token = ""
        try:
            # Try to extract from meta tags or JS variables
            x_api_key = sb.execute_script("""
                // Try meta tag
                var meta = document.querySelector('meta[name="x-api-key"]');
                if (meta) return meta.content;
                // Try window config
                if (window.__CONFIG__ && window.__CONFIG__.apiKey) return window.__CONFIG__.apiKey;
                if (window.__NEXT_DATA__) {
                    var s = JSON.stringify(window.__NEXT_DATA__);
                    var m = s.match(/x-api-key["']\\s*:\\s*["']([^"']+)/);
                    if (m) return m[1];
                }
                return '';
            """) or ""
            x_csrf_token = sb.execute_script("""
                var meta = document.querySelector('meta[name="csrf-token"]');
                if (meta) return meta.content;
                // Try cookie
                var cookies = document.cookie.split(';');
                for (var i = 0; i < cookies.length; i++) {
                    var c = cookies[i].trim();
                    if (c.startsWith('XSRF-TOKEN=') || c.startsWith('csrf-token=')) return c.split('=')[1];
                }
                return '';
            """) or ""
            
            # If we couldn't find them, try intercepting an XHR to get the headers
            if not x_api_key:
                result = sb.execute_script("""
                    // Make a fetch to the catalog to capture the headers the app uses
                    try {
                        var xhr = new XMLHttpRequest();
                        xhr.open('GET', '/tnwr/v1/secure/catalog?maxPerformances=1&maxTimeslots=1&maxPerformanceDays=1&maxTimeslotDays=1&includeMetadata=true', false);
                        xhr.setRequestHeader('Accept', 'application/json');
                        xhr.send();
                        return xhr.responseText;
                    } catch(e) { return ''; }
                """)
                if result:
                    logging.info(f" Direct XHR test returned {len(result)} chars")
            
            logging.info(f" x-api-key: {x_api_key[:20]}..." if x_api_key else " No x-api-key found")
            logging.info(f" x-csrf-token: {x_csrf_token[:20]}..." if x_csrf_token else " No x-csrf-token found")
        except Exception as e:
            logging.warning(f" Could not extract API headers: {e}")
        
        # Set up headers matching the working browser request
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en",
            "Referer": "https://ticketsale.wimbledon.com/secured/content",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }
        if x_api_key:
            headers["x-api-key"] = x_api_key
        else:
            # Fallback to known API key
            headers["x-api-key"] = "344152a6-fa57-4e09-951f-96b8a38927d9"
            logging.info(" Using fallback x-api-key")
        if x_csrf_token:
            headers["x-csrf-token"] = x_csrf_token
        
        # Update session headers
        session.headers.update(headers)
        
        logging.info(f" Extracted {len(cookies)} cookies")
        logging.info(f" User-Agent: {user_agent[:50]}...")
        
        return session, headers
        
    except Exception as e:
        logging.error(f" Error creating session from SeleniumBase: {e}")
        return None, None


def make_api_request(driver):
    """
    Extract cookies and make API request to catalog endpoint
    Enhanced with better debugging for 403 errors
    """
    print("\n Extracting session cookies for API requests...")
    session, headers = create_session_from_driver(driver)

    # Additional check to ensure we're on the right page
    current_url = driver.current_url
    print(f" Current page URL: {current_url}")
    
    if "content" not in current_url:
        print(" Not on content page, navigating there first...")
        driver.get("https://ticketsale.wimbledon.com/content")
        time.sleep(3)
        # Re-extract session after navigation
        session, headers = create_session_from_driver(driver)

    # Make the API request
    api_url = "https://ticketsale.wimbledon.com/tnwr/v1/secure/catalog"
    params = {
        "maxPerformances": 50,
        "maxTimeslots": 50,
        "maxPerformanceDays": 3,
        "maxTimeslotDays": 3,
        "includeMetadata": "true",
    }

    print(f"\n Making API request to: {api_url}")
    print(f" Request parameters: {params}")
    
    try:
        # Add request timeout and additional debug info
        api_response = session.get(
            api_url, 
            headers=headers, 
            params=params, 
            verify=False,
            timeout=30
        )
        
        print(f" API Response Status: {api_response.status_code}")
        print(f" Response headers: {dict(api_response.headers)}")

        if api_response.status_code == 200:
            response_text = api_response.text
            print(f" Response length: {len(response_text)} characters")
            
            # Check if response is empty or just whitespace
            if not response_text.strip():
                print(" WARNING: API returned empty response!")
                return None, None
            
            # Save the API response
            with open("catalog_response.json", "w", encoding="utf-8") as f:
                f.write(response_text)
            print(" API response saved to 'catalog_response.json'")

            # Print a preview of the response
            try:
                response_data = api_response.json()
                print(f"\n API Response Preview:")
                if isinstance(response_data, dict):
                    print(f"  Response keys: {list(response_data.keys())}")
                    if len(response_data) > 0:
                        for key, value in list(response_data.items())[:3]:  # Show first 3 keys
                            print(f"    {key}: {str(value)[:100]}{'...' if len(str(value)) > 100 else ''}")
                else:
                    print(f"  Response type: {type(response_data)}")
                    print(f"  Response preview: {str(response_data)[:200]}...")
            except Exception as e:
                print(f" Could not parse JSON response: {e}")
                print(f"Raw response preview: {response_text[:200]}...")

            return session, headers
            
        elif api_response.status_code == 403:
            print(" 403 Forbidden Error - Anti-bot protection triggered!")
            print("This usually means:")
            print("   Missing critical cookies (datadome, bm_sv, ak_bmsc)")
            print("   Incorrect CSRF token")
            print("   Browser fingerprint mismatch")
            print("   Need to wait longer for session stabilization")
            print(f"\n Response content: {api_response.text[:500]}...")
            return None, None
            
        else:
            print(f" API request failed with status {api_response.status_code}")
            print(f" Response headers: {dict(api_response.headers)}")
            print(f" Response content: {api_response.text[:500]}...")
            return None, None
            
    except requests.exceptions.Timeout:
        print(" API request timed out after 30 seconds")
        return None, None
    except Exception as e:
        print(f" API request error: {e}")
        return None, None

    except Exception as api_error:
        print(f" Error making API request: {api_error}")
        return None, None


def send_discord_webhook(day_name, buy_link, advantage_name):
    """
    Send Discord webhook notification for available tickets
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook_url:
        print(" No Discord webhook URL found in environment variables!")
        print("Please set DISCORD_WEBHOOK_URL in your .env file")
        return False

    # Log which webhook we're posting to (masked for security)
    masked = webhook_url[:60] + "..." if len(webhook_url) > 60 else webhook_url
    print(f" Sending webhook to: {masked}")

    embed = {
        "title": " Wimbledon Tickets Available!",
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
            "text": f"Wimbledon Ticket Monitor - {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print(f" Discord notification sent for {day_name}")
            return True
        else:
            print(f" Failed to send Discord notification: {response.status_code}")
            return False
    except Exception as e:
        print(f" Error sending Discord notification: {e}")
        return False


def automated_add_to_cart_sb(sb, buy_link, day_name, start_time=None):
    """
     UPDATED: Navigate to buy link and automatically add 2 tickets to cart using SeleniumBase
    """
    try:
        if start_time is None:
            start_time = time.time()

        logging.info(f" Starting automated purchase for {day_name}")
        logging.info(f"   Navigating to: {buy_link}")

        # Navigate to the buy page using SeleniumBase
        navigation_start = time.time()
        sb.open(buy_link)
        navigation_time = time.time() - navigation_start
        logging.info(f"    Page navigation took: {navigation_time:.2f} seconds")
        
        # Wait for page to load
        sb.wait_for_element("table", timeout=10)
        logging.info("   Buy page loaded, looking for available tickets...")

        # Look for the first available quantity dropdown (not sold out)
        quantity_selects = sb.find_elements("td.quantity select")

        ticket_added = False
        for select in quantity_selects:
            try:
                if sb.is_element_visible(select):
                    # Check if parent row is not sold out
                    parent_row = sb.find_element("./ancestor::tr[1]", select)
                    if "category_unavailable" not in sb.get_attribute(parent_row, "class"):
                        logging.info("   Found available ticket category, setting quantity to 2...")
                        
                        # Use SeleniumBase to set the value
                        sb.execute_script(
                            "arguments[0].value = '2'; arguments[0].dispatchEvent(new Event('change', { bubbles: true })); arguments[0].dispatchEvent(new Event('input', { bubbles: true }));",
                            select,
                        )

                        logging.info(" Quantity set to 2")
                        ticket_added = True
                        break
            except Exception as e:
                logging.info(f"   Skipping select element: {e}")
                continue

        if not ticket_added:
            logging.info(" No available tickets found on the page")
            return False

        # Look for and click the "Add to cart" button
        cart_process_start = time.time()
        logging.info("   Looking for 'Add to cart' button...")

        try:
            # Wait for the add to cart button
            sb.wait_for_element("#book", timeout=5)

            # Try multiple selectors for the add to cart button
            add_to_cart_selectors = [
                "#book",
                "#addToCartButtonContainer a",
                "a[onclick*='validateQuantities']",
                ".stx_tfooter_buttons_container a",
            ]

            add_to_cart_button = None
            for selector in add_to_cart_selectors:
                try:
                    if sb.is_element_present(selector) and sb.is_element_visible(selector):
                        add_to_cart_button = selector
                        logging.info(f"   Found add to cart button using selector: {selector}")
                        break
                except:
                    continue

            if add_to_cart_button:
                logging.info("   Clicking 'Add to cart' button...")
                sb.click(add_to_cart_button)
                logging.info(" Add to cart button clicked!")

                # Wait for result
                time.sleep(0.5)

                # Check for error messages
                error_selectors = [".message.error", ".error"]
                has_errors = False

                for selector in error_selectors:
                    try:
                        if sb.is_element_present(selector) and sb.is_element_visible(selector):
                            error_text = sb.get_text(selector)
                            if error_text.strip():
                                logging.info(f" Error message detected: {error_text}")
                                has_errors = True

                                if "insufficient tickets" in error_text.lower():
                                    logging.info("   -> Insufficient tickets available")
                                    return False
                                elif "no longer available" in error_text.lower():
                                    logging.info("   -> Tickets no longer available")
                                    return False
                                elif "sold out" in error_text.lower():
                                    logging.info("   -> Tickets sold out")
                                    return False
                    except:
                        continue

                if has_errors:
                    logging.info(" Purchase failed due to errors on page")
                    return False

                # Check if we were successful
                current_url = sb.get_current_url()
                if "cart" in current_url.lower() or "basket" in current_url.lower():
                    cart_process_time = time.time() - cart_process_start
                    total_time = time.time() - start_time
                    logging.info(" Successfully added tickets to cart! (URL indicates cart page)")
                    logging.info(f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s")
                    return True

                # Additional success indicators
                success_indicators = [
                    ".cart", ".basket", "[class*='cart']", "[id*='cart']",
                    ".success", ".confirmation", "[class*='success']",
                ]

                for indicator in success_indicators:
                    try:
                        if sb.is_element_present(indicator) and sb.is_element_visible(indicator):
                            cart_process_time = time.time() - cart_process_start
                            total_time = time.time() - start_time
                            logging.info(f" Success indicator found: {indicator}")
                            logging.info(f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s")
                            return True
                    except:
                        continue

                # Check for non-zero amounts
                try:
                    amount_selectors = [".reservation_amount", "[data-amount]", ".amount"]
                    for selector in amount_selectors:
                        if sb.is_element_present(selector) and sb.is_element_visible(selector):
                            amount_text = sb.get_attribute(selector, "data-amount") or sb.get_text(selector)
                            if amount_text and amount_text != "0" and "£0" not in amount_text:
                                cart_process_time = time.time() - cart_process_start
                                total_time = time.time() - start_time
                                logging.info(f" Non-zero amount detected: {amount_text}")
                                logging.info(f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s")
                                return True
                except:
                    pass

                logging.info(f" Uncertain result - Current URL: {current_url}")
                return False
            else:
                logging.info(" Could not find 'Add to cart' button")
                return False

        except Exception as e:
            logging.info(f" Error clicking submit button: {e}")
            return False

    except Exception as e:
        logging.info(f" Error during automated purchase: {e}")
        return False
    """
    Navigate to buy link and automatically add 2 tickets to cart
    """
    try:
        if start_time is None:
            start_time = time.time()

        print(f" Starting automated purchase for {day_name}")
        print(f"   Navigating to: {buy_link}")

        # Navigate to the buy page
        navigation_start = time.time()
        driver.get(buy_link)
        navigation_time = time.time() - navigation_start
        print(f"    Page navigation took: {navigation_time:.2f} seconds")
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

                        print(" Quantity set to 2")
                        ticket_added = True
                        break
            except Exception as e:
                print(f"   Skipping select element: {e}")
                continue

        if not ticket_added:
            print(" No available tickets found on the page")
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
                    print(" Add to cart button clicked via JavaScript!")
                except:
                    # Fallback to regular click                    add_to_cart_button.click()
                    print(" Add to cart button clicked via regular click!")

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
                        print(f" Error message detected: {error_text}")
                        has_errors = True

                        # Check for specific error types
                        if "insufficient tickets" in error_text.lower():
                            print("   -> Insufficient tickets available")
                            return False
                        elif "no longer available" in error_text.lower():
                            print("   -> Tickets no longer available")
                            return False
                        elif "sold out" in error_text.lower():
                            print("   -> Tickets sold out")
                            return False

                if has_errors:
                    print(" Purchase failed due to errors on page")
                    return False
                # Check if we were successful (look for cart page or confirmation)
                current_url = driver.current_url
                if "cart" in current_url.lower() or "basket" in current_url.lower():
                    cart_process_time = time.time() - cart_process_start
                    total_time = time.time() - start_time
                    print(
                        " Successfully added tickets to cart! (URL indicates cart page)"
                    )
                    print(
                        f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
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
                            print(f" Success indicator found: {indicator}")
                            print(
                                f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
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
                                print(f" Non-zero amount detected: {amount_text}")
                                print(
                                    f"    Cart process: {cart_process_time:.3f}s | Total time: {total_time:.3f}s"
                                )
                                return True
                except:
                    pass

                # If no clear success indicators and no errors, it's uncertain
                print(f" Uncertain result - Current URL: {current_url}")
                print(
                    "   No clear error messages, but no definitive success indicators either"
                )
                return False  # Changed from True to False - be conservative
            else:
                print(" Could not find 'Add to cart' button")
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
            print(f" Error clicking submit button: {e}")
            return False

    except Exception as e:
        print(f" Error during automated purchase: {e}")
        return False


def send_test_webhook():
    """
    Send a test Discord webhook notification to verify setup
    """
    # Test the LOG webhook
    webhook_url = DISCORD_LOG_WEBHOOK_URL

    if not webhook_url:
        print(" No log webhook URL configured!")
        return False

    embed = {
        "title": " Wimbledon Monitor Test",
        "description": "Test notification",
        "color": 0x0099FF,  # Blue color
        "fields": [
            {"name": "Status", "value": " Webhook Connected", "inline": True},
            {"name": "Monitoring", "value": " ", "inline": True},
        ],
        "footer": {
            "text": f"Wimbledon Ticket Monitor - Test Message - {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        if response.status_code == 204:
            print(" Test Discord notification sent successfully!")
        else:
            print(
                f" Failed to send test Discord notification: {response.status_code}"
            )
            return False
    except Exception as e:
        print(f" Error sending test Discord notification: {e}")
        return False

    # Verify restock webhook URL is configured (don't send test message to it)
    restock_webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not restock_webhook_url:
        print(" No DISCORD_WEBHOOK_URL found in .env! Restock notifications will NOT be sent!")
        return False
    
    masked_url = restock_webhook_url[:60] + "..." if len(restock_webhook_url) > 60 else restock_webhook_url
    print(f" Restock webhook URL configured: {masked_url}")
    return True


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
                "name": " Speed",
                "value": f"{total_time:.3f} seconds from detection to cart",
                "inline": False,
            }
        ]

    embed = {
        "title": " Added to Cart Successfully!",
        "description": f"Tickets successfully added to cart for **{day_name}**",
        "color": 0x00FF00,  # Green color
        "fields": [
            {"name": "Event", "value": day_name, "inline": True},
            {"name": "Advantage Type", "value": advantage_name, "inline": True},
            {
                "name": "Status",
                "value": " Tickets confirmed in cart - proceed to checkout!",
                "inline": False,
            },
        ]
        + timing_field,
        "footer": {
            "text": f"Wimbledon Automation - SUCCESS - {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
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
                "name": " Time Spent",
                "value": f"{total_time:.3f} seconds attempting purchase",
                "inline": False,
            }
        ]

    embed = {
        "title": " Purchase Attempt Failed",
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
            "text": f"Wimbledon Automation - {datetime.now(UTC).strftime('%d. %m. %Y %H:%M:%S')}"
        },
    }

    payload = {"embeds": [embed]}

    try:
        response = requests.post(webhook_url, json=payload)
        return response.status_code == 204
    except:
        return False


def _log_failed_request(status, status_text='', response_headers='', response_body='', url='', source='', request_headers=None):
    """Append a failed request's details to failed_requests.log for analysis."""
    import json as json_mod
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": timestamp,
        "source": source,
        "url": url,
        "status": status,
        "status_text": status_text,
        "response_headers": response_headers if isinstance(response_headers, (dict, str)) else str(response_headers),
        "response_body": response_body,
    }
    if request_headers:
        entry["request_headers"] = request_headers
    try:
        with open("failed_requests.log", "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(json_mod.dumps(entry, indent=2, default=str) + "\n")
        logging.info(f" Failed request logged to failed_requests.log (status {status})")
    except Exception:
        pass


def monitor_performances(session, headers, known_states=None, sb=None, automation_enabled=True, proxy=None, last_response=None):
    """
     UPDATED: Monitor performances for availability changes in Centre Court events
    Now stores full response and diffs against previous to catch ANY change
    """
    if known_states is None:
        known_states = {}

    try:
        # Use browser fetch to make the API call (inherits all auth cookies/headers)
        if sb:
            # Make sure we're on the right page first
            try:
                current_url = sb.get_current_url()
                if 'ticketsale.wimbledon.com' not in current_url:
                    logging.info(" Browser navigated away, returning to content page...")
                    sb.open("https://ticketsale.wimbledon.com/secured/content")
                    time.sleep(3)
            except Exception:
                pass
            
            try:
                fetch_result = sb.execute_async_script("""
                    var callback = arguments[arguments.length - 1];
                    fetch('/tnwr/v1/secure/catalog?maxPerformances=50&maxTimeslots=50&maxPerformanceDays=3&maxTimeslotDays=3&includeMetadata=true', {
                        method: 'GET',
                        headers: {
                            'Accept': 'application/json, text/plain, */*',
                            'Accept-Language': 'en',
                            'x-api-key': '344152a6-fa57-4e09-951f-96b8a38927d9',
                            'x-secutix-host': 'ticketsale.wimbledon.com'
                        },
                        credentials: 'include'
                    }).then(function(response) {
                        if (response.ok) {
                            return response.text().then(function(text) { callback(text); });
                        } else {
                            return response.text().then(function(body) {
                                callback(JSON.stringify({
                                    error: true,
                                    status: response.status,
                                    statusText: response.statusText,
                                    responseBody: body.substring(0, 5000),
                                    url: '/tnwr/v1/secure/catalog'
                                }));
                            });
                        }
                    }).catch(function(e) {
                        callback(JSON.stringify({error: true, status: 0, statusText: e.message}));
                    });
                """)
            except Exception as xhr_err:
                logging.warning(f" XHR execution failed: {xhr_err}")
                # Browser context may be broken, try navigating back
                try:
                    sb.open("https://ticketsale.wimbledon.com/secured/content")
                    time.sleep(3)
                except Exception:
                    pass
                return known_states, automation_enabled, last_response

            if not fetch_result:
                print(" Browser fetch returned empty result")
                return known_states, automation_enabled, last_response

            import json as json_mod
            parsed = json_mod.loads(fetch_result)
            if isinstance(parsed, dict) and parsed.get("error"):
                status = parsed.get('status', 'unknown')
                print(f" API request failed with status {status}")
                
                # Log failed request to file for analysis
                _log_failed_request(
                    status=status,
                    status_text=parsed.get('statusText', ''),
                    response_headers=parsed.get('responseHeaders', ''),
                    response_body=parsed.get('responseBody', ''),
                    url=parsed.get('url', '/tnwr/v1/secure/catalog'),
                    source='browser_xhr'
                )
                
                # DataDome 403 = banned, restart with new proxy
                if status == 403:
                    logging.info(" DataDome ban detected! Restarting with new proxy...")
                    send_datadome_ban_webhook(proxy)
                    raise DataDomeBanError("DataDome 403 ban")
                
                return known_states, automation_enabled, last_response
            data = parsed
        else:
            # Fallback to requests session
            api_url = "https://ticketsale.wimbledon.com/tnwr/v1/secure/catalog"
            params = {
                "maxPerformances": 50,
                "maxTimeslots": 50,
                "maxPerformanceDays": 3,
                "maxTimeslotDays": 3,
                "includeMetadata": "true",
            }
            response = session.get(api_url, headers=headers, params=params, verify=False)
            if response.status_code != 200:
                print(f" API request failed with status {response.status_code}")
                _log_failed_request(
                    status=response.status_code,
                    status_text=response.reason,
                    response_headers=dict(response.headers),
                    response_body=response.text[:5000],
                    url=str(response.url),
                    source='requests_session',
                    request_headers=dict(response.request.headers)
                )
                return known_states, automation_enabled, last_response
            data = response.json()

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        #  Full response diff 
        import json as json_mod

        if last_response is None:
            print(f"[{current_time}]  First response captured (baseline). Monitoring for ANY changes...")
            # Dump a summary of what we see
            sections = data.get("sections", [])
            perf_count = 0
            for section in sections:
                for cluster in section.get("clusters", []):
                    for item in cluster.get("items", []):
                        for perf in item.get("product", {}).get("performances", []):
                            perf_count += 1
                            name = perf.get("name", {}).get("en", "")
                            avail = perf.get("availability", "NONE")
                            known_states[f"{perf.get('performanceId')}"] = avail
            print(f"   Found {perf_count} performances in response")
        else:
            # Deep compare: diff the entire JSON
            def find_diffs(old, new, path=""):
                """Recursively find all differences between two JSON structures"""
                diffs = []
                if type(old) != type(new):
                    diffs.append((path, old, new))
                    return diffs
                if isinstance(old, dict):
                    all_keys = set(list(old.keys()) + list(new.keys()))
                    for key in all_keys:
                        child_path = f"{path}.{key}" if path else key
                        if key not in old:
                            diffs.append((child_path, "<missing>", new[key]))
                        elif key not in new:
                            diffs.append((child_path, old[key], "<removed>"))
                        else:
                            diffs.extend(find_diffs(old[key], new[key], child_path))
                elif isinstance(old, list):
                    if len(old) != len(new):
                        diffs.append((f"{path}[len]", len(old), len(new)))
                    for i in range(min(len(old), len(new))):
                        diffs.extend(find_diffs(old[i], new[i], f"{path}[{i}]"))
                    # Items added
                    for i in range(len(old), len(new)):
                        diffs.append((f"{path}[{i}]", "<missing>", new[i]))
                    # Items removed
                    for i in range(len(new), len(old)):
                        diffs.append((f"{path}[{i}]", old[i], "<removed>"))
                else:
                    if old != new:
                        diffs.append((path, old, new))
                return diffs

            diffs = find_diffs(last_response, data)

            # Filter out known maintenance cycle noise (fields that toggle every ~15 min)
            MAINTENANCE_NOISE_KEYWORDS = {
                'lowPrice', 'highPrice', 'salesEndDate', 'withAdvantage',
                'advantages[len]', 'advantages[0]', 'advantages[1]',
                'advantages[2]', 'advantages[3]',
            }

            def is_maintenance_noise(path):
                """Check if a diff path is just the known maintenance toggle"""
                for keyword in MAINTENANCE_NOISE_KEYWORDS:
                    if keyword in path:
                        return True
                return False

            meaningful_diffs = [d for d in diffs if not is_maintenance_noise(d[0])]
            noise_diffs = len(diffs) - len(meaningful_diffs)

            if diffs:
                if meaningful_diffs:
                    print(f"[{current_time}]  RESPONSE CHANGED! {len(meaningful_diffs)} meaningful + {noise_diffs} maintenance diff(s)")
                    for path, old_val, new_val in meaningful_diffs[:20]:
                        old_str = str(old_val)[:100]
                        new_str = str(new_val)[:100]
                        print(f"    {path}: {old_str} -> {new_str}")
                    if len(meaningful_diffs) > 20:
                        print(f"   ... and {len(meaningful_diffs) - 20} more changes")
                    # No webhook for diffs - only availability webhooks below
                else:
                    print(f"[{current_time}]  Maintenance cycle ({noise_diffs} toggled fields, ignored)")
            else:
                print(f"[{current_time}]  No changes in response (identical to previous)")

        #  Availability logic (restock detection + ATC) 
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
                        perf_id = performance.get("performanceId")
                        buy_link = performance.get("action", {}).get("buy", "")
                        current_availability = performance.get("availability", "NONE")

                        # Create unique key for this performance
                        state_key = f"{perf_id}"

                        # Check if this is a new state or changed from NONE
                        previous_availability = known_states.get(
                            state_key, "NONE"
                        )

                        if (
                            previous_availability == "NONE"
                            and current_availability != "NONE"
                        ):
                            print(f" RESTOCK!")
                            print(f"   Event: {name}")
                            print(
                                f"   Status: {previous_availability} -> {current_availability}"
                            )
                            print(f"   Buy Link: {buy_link}")

                            send_discord_webhook(name, buy_link, name)
                            changes_found = True

                        elif (
                            previous_availability != "NONE"
                            and current_availability == "NONE"
                            and state_key in known_states
                        ):
                            print(f" SOLD OUT!")
                            print(f"   Event: {name}")
                            print(
                                f"   Status: {previous_availability} -> {current_availability}"
                            )

                        # Update known state
                        known_states[state_key] = current_availability

        if not changes_found and last_response is not None:
            pass  # Already printed diff status above

        return known_states, automation_enabled, data  # data becomes next call's last_response

    except DataDomeBanError:
        raise  # Must propagate to trigger restart with new proxy
    except Exception as e:
        import traceback
        print(f" Error during monitoring: {e}")
        traceback.print_exc()
        return known_states, automation_enabled, last_response


def start_monitoring(session, headers, sb=None, proxy=None):
    """
     UPDATED: Start the continuous monitoring loop with SeleniumBase for automated purchasing
    """
    logging.info("\n Starting Wimbledon ticket monitoring...")
    logging.info("Monitoring ALL courts for availability changes...")
    if sb:
        logging.info(" Automated purchasing enabled with undetectable browser!")
    else:
        logging.info(" Notifications only (no automated purchasing)")
    logging.info("Checking every 10 seconds. Press Ctrl+C to stop.\n")

    known_states = {}
    last_response = None  # Store full previous response for diffing
    automation_enabled = True  # Flag to control automated purchasing

    try:
        while True:
            known_states, automation_enabled, last_response = monitor_performances(
                session, headers, known_states, sb, automation_enabled, proxy=proxy,
                last_response=last_response
            )
            # Sleep 5 seconds between checks
            time.sleep(5)
    except KeyboardInterrupt:
        logging.info("\n Monitoring stopped by user.")
    except DataDomeBanError:
        raise  # Let main() handle the restart
    except Exception as e:
        logging.info(f"\n Monitoring error: {e}")


def create_session_from_driver(driver):
    """
    Create a requests session with cookies from the browser driver
    Enhanced to properly match working API requests
    """
    print(" Waiting for browser session to fully stabilize...")
    time.sleep(5)  # Give more time for all anti-bot cookies to be set
    
    # Force page refresh to ensure all security cookies are loaded
    driver.execute_script("if (!window.location.href.includes('content')) window.location.reload();")
    time.sleep(2)
    
    # Get all cookies from the browser
    cookies = driver.get_cookies()
    session = requests.Session()

    # Apply proxy to requests session if configured
    proxy_list = load_proxies()
    proxy = pick_proxy(proxy_list)
    if proxy:
        if proxy["user"] and proxy["pass"]:
            proxy_url = f"http://{proxy['user']}:{proxy['pass']}@{proxy['ip']}:{proxy['port']}"
        else:
            proxy_url = f"http://{proxy['ip']}:{proxy['port']}"
        session.proxies = {"http": proxy_url, "https": proxy_url}
        print(f" Requests session using proxy: {proxy['ip']}:{proxy['port']}")

    print(f" Found {len(cookies)} cookies in browser")
    
    # Copy ALL cookies with proper domain handling
    for cookie in cookies:
        try:
            session.cookies.set(
                cookie["name"], 
                cookie["value"], 
                domain=cookie.get("domain", ".wimbledon.com"),
                path=cookie.get("path", "/"),
                secure=cookie.get("secure", False),
                rest={'HttpOnly': cookie.get("httpOnly", False)}
            )
        except Exception as e:
            print(f" Could not set cookie {cookie['name']}: {e}")
    
    # Check for critical anti-bot and session cookies
    critical_cookies = {
        'STX_SESSION': 'Secutix session ID - REQUIRED for API calls',
        'stx_contact_AELTC_B2C_v1': 'Contact session - REQUIRED for authentication',
        'datadome': 'DataDome anti-bot protection',
        'bm_sv': 'Bot management system',
        'ak_bmsc': 'Akamai bot manager',
    }
    
    print("\n Critical cookie status:")
    missing_critical = []
    session_cookies_found = 0
    
    for cookie_name, description in critical_cookies.items():
        cookie_value = session.cookies.get(cookie_name)
        if cookie_value:
            print(f"   {cookie_name}: {cookie_value[:25]}... ({description})")
            if cookie_name in ['STX_SESSION', 'stx_contact_AELTC_B2C_v1']:
                session_cookies_found += 1
        else:
            print(f"   {cookie_name}: MISSING ({description})")
            missing_critical.append(cookie_name)
    
    # Critical check for session cookies
    if session_cookies_found < 2:
        print(f"\n CRITICAL WARNING: Missing session cookies!")
        print(f"   Found {session_cookies_found}/2 required session cookies")
        print(f"   This indicates you may not be properly logged in")
        print(f"   The API will likely return 403 Forbidden")
        print(f"   Please ensure you:")
        print(f"   1. Completed login successfully")
        print(f"   2. Can see account/logout links on the page")
        print(f"   3. Are not in guest/anonymous mode")
    
    if missing_critical:
        print(f"\n WARNING: Missing critical cookies: {missing_critical}")
        if 'datadome' in missing_critical:
            print("   DataDome cookie missing - anti-bot protection may block requests")
        print("   Consider waiting longer after login or refreshing the page")

    # Get the exact user agent from the browser to avoid fingerprinting detection
    user_agent = driver.execute_script("return navigator.userAgent;")
    print(f" Using browser user agent: {user_agent}")
    
    # Try to capture the exact headers from a successful browser request
    print("\n Attempting to capture exact working headers...")
    
    try:
        # Execute a test fetch in the browser to see what headers it uses
        working_headers = driver.execute_script("""
            return new Promise((resolve) => {
                // Create a fetch request and capture the headers
                const originalFetch = window.fetch;
                let capturedHeaders = null;
                
                // Temporarily override fetch to capture headers
                window.fetch = function(...args) {
                    if (args[1] && args[1].headers) {
                        capturedHeaders = args[1].headers;
                    }
                    return originalFetch.apply(this, args);
                };
                
                // Make a test request (this will be intercepted)
                fetch('/tnwr/v1/secure/catalog?maxPerformances=1', {
                    method: 'GET',
                    headers: {
                        'accept': 'application/json, text/plain, */*',
                        'accept-language': 'en',
                        'cache-control': 'no-cache',
                        'pragma': 'no-cache',
                        'x-api-key': '344152a6-fa57-4e09-951f-96b8a38927d9',
                        'x-secutix-host': 'ticketsale.wimbledon.com'
                    },
                    credentials: 'include'
                }).then(() => {
                    // Restore original fetch
                    window.fetch = originalFetch;
                    resolve(capturedHeaders);
                }).catch(() => {
                    window.fetch = originalFetch;
                    resolve(null);
                });
            });
        """)
        
        if working_headers:
            print(" Captured working headers from browser!")
            headers = working_headers
        else:
            print(" Could not capture browser headers, using default set")
            headers = {}
            
    except Exception as e:
        print(f" Error capturing browser headers: {e}")
        headers = {}
    
    # Ensure we have all the essential headers
    essential_headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://ticketsale.wimbledon.com/content",
        "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": user_agent,
        "x-api-key": "344152a6-fa57-4e09-951f-96b8a38927d9",
        "x-secutix-host": "ticketsale.wimbledon.com",
    }
    
    # Merge captured headers with essential headers (essential headers take precedence)
    for key, value in essential_headers.items():
        headers[key] = value
    
    # Enhanced CSRF token extraction with API endpoint
    csrf_token = None
    print("\n Getting CSRF token from API endpoint...")
    
    # First, try the dedicated CSRF endpoint (newly discovered!)
    try:
        csrf_url = "https://ticketsale.wimbledon.com/tnwr/v1/csrf"
        csrf_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://ticketsale.wimbledon.com/content",
            "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": user_agent,
            "x-api-key": "344152a6-fa57-4e09-951f-96b8a38927d9",
            "x-secutix-host": "ticketsale.wimbledon.com",
        }
        
        print(f" Requesting CSRF token from: {csrf_url}")
        csrf_response = session.get(csrf_url, headers=csrf_headers, timeout=15)
        
        print(f" CSRF endpoint response: {csrf_response.status_code}")
        
        if csrf_response.status_code == 200:
            # The CSRF token should be in the response
            try:
                csrf_data = csrf_response.json()
                if isinstance(csrf_data, dict) and 'token' in csrf_data:
                    csrf_token = csrf_data['token']
                    print(f" CSRF token from API response: {csrf_token}")
                elif isinstance(csrf_data, dict) and 'csrf' in csrf_data:
                    csrf_token = csrf_data['csrf']
                    print(f" CSRF token from API response (csrf field): {csrf_token}")
                else:
                    # Sometimes the entire response is just the token
                    if isinstance(csrf_data, str) and len(csrf_data) == 36:
                        csrf_token = csrf_data
                        print(f" CSRF token from API response (direct): {csrf_token}")
                    else:
                        print(f" CSRF API returned unexpected format: {csrf_data}")
            except json.JSONDecodeError:
                # Maybe the response is just the token as plain text
                response_text = csrf_response.text.strip()
                if len(response_text) == 36 and '-' in response_text:
                    csrf_token = response_text
                    print(f" CSRF token from API response (plain text): {csrf_token}")
                else:
                    print(f" CSRF API returned non-JSON: {response_text[:100]}")
        else:
            print(f" CSRF endpoint failed: {csrf_response.status_code}")
            print(f"Response: {csrf_response.text[:200]}")
            
    except Exception as e:
        print(f" Error calling CSRF endpoint: {e}")
    
    # Fallback to browser-based extraction if API didn't work
    if not csrf_token:
        print("\n Falling back to browser-based CSRF extraction...")
        
        # Check if we're logged in by looking for account indicators
        current_page_url = driver.current_url
        print(f"Current page: {current_page_url}")
        
        login_check = driver.execute_script("""
            const pageText = document.body.innerText.toLowerCase();
            const indicators = ['account', 'logout', 'profile', 'myaccount', 'welcome', 'dashboard'];
            return indicators.some(indicator => pageText.includes(indicator));
        """)
        
        if not login_check:
            print(" WARNING: No login indicators found - you may not be fully logged in!")
            print("   Make sure you can see account/logout links before continuing")
        
        try:
            # Method 1: Check window.csrfToken
            csrf_token = driver.execute_script("return window.csrfToken")
            if csrf_token:
                print(f" CSRF token from window.csrfToken: {csrf_token}")
            else:
                # Method 2: Check meta tag
                csrf_token = driver.execute_script(
                    "return document.querySelector('meta[name=\"csrf-token\"]')?.content"
                )
                if csrf_token:
                    print(f" CSRF token from meta tag: {csrf_token}")
                else:
                    # Method 3: Search in script tags for UUID patterns
                    csrf_token = driver.execute_script("""
                        const scripts = document.getElementsByTagName('script');
                        const patterns = [
                            /csrfToken['"\\s]*[:=]['"\\s]*([a-f0-9-]{36})/i,
                            /csrf['"\\s]*[:=]['"\\s]*([a-f0-9-]{36})/i,
                            /_token['"\\s]*[:=]['"\\s]*([a-f0-9-]{36})/i,
                            /['"](([a-f0-9]{8}-){4}[a-f0-9]{8})['"]/
                        ];
                        
                        for (let script of scripts) {
                            if (script.textContent) {
                                for (let pattern of patterns) {
                                    const match = script.textContent.match(pattern);
                                    if (match) return match[1];
                                }
                            }
                        }
                        return null;
                    """)
                    if csrf_token:
                        print(f" CSRF token from script: {csrf_token}")
                        
        except Exception as e:
            print(f" Error during browser CSRF extraction: {e}")
    
    if csrf_token:
        headers["x-csrf-token"] = csrf_token
    else:
        print(" CRITICAL: No CSRF token found!")
        print("   This will likely cause 403 errors")
        print("   Make sure you're fully logged in and try:")
        print("   1. Navigate to a ticket purchase page manually")
        print("   2. Check browser console for JavaScript errors")
        print("   3. Verify you can see account/logout links")

    # Print summary of critical headers
    print(f"\n Request headers summary:")
    print(f"  x-api-key: {headers.get('x-api-key', 'MISSING')}")
    print(f"  x-csrf-token: {headers.get('x-csrf-token', 'MISSING')}")
    print(f"  x-secutix-host: {headers.get('x-secutix-host', 'MISSING')}")
    print(f"  referer: {headers.get('referer', 'MISSING')}")
    print(f"  user-agent: {headers.get('user-agent', 'MISSING')[:50]}...")

    return session, headers


def setup_undetectable_browser(proxy=None):
    """
     NEW: Configure and return undetectable Chrome with SeleniumBase
    This replaces the old Selenium setup with enterprise-grade anti-detection
    """
    browser_manager = UndetectableBrowser()
    config = browser_manager.create_undetectable_browser()
    
    # Apply proxy if provided
    if proxy:
        config = browser_manager.apply_proxy(config, proxy)
        logging.info(f" Browser will use proxy: {proxy['ip']}:{proxy['port']}")
    
    logging.info(" Starting undetectable Chrome browser...")
    logging.info(" Using SeleniumBase with undetected-chrome technology")
    
    return config, browser_manager


def main():
    """
     UPDATED: Main function using undetectable Chrome technology
    """
    logging.info(" Starting Wimbledon Undetectable Ticket Monitor")
    logging.info(" Using enterprise-grade anti-detection technology")
    
    # Load and pick a proxy
    proxy_list = load_proxies()
    proxy = pick_proxy(proxy_list)
    if proxy:
        logging.info(f" Selected proxy: {proxy['ip']}:{proxy['port']}")
    else:
        logging.info(" No proxy selected, using direct connection")
    
    # Get browser configuration
    browser_config, browser_manager = setup_undetectable_browser(proxy=proxy)
    
    #  THE MAGIC: Create undetectable browser with SeleniumBase
    with SB(**browser_config) as sb:
        logging.info(" Undetectable Chrome browser started successfully")
        
        # Increase script timeout to avoid XHR timeouts during monitoring
        sb.driver.set_script_timeout(120)
        logging.info(" Script timeout set to 120 seconds")
        
        # Block WebRTC IP leaks
        try:
            sb.execute_script("""
                // Override WebRTC to prevent real IP leak
                if (window.RTCPeerConnection) {
                    window.RTCPeerConnection = function() { return {}; };
                }
                if (window.webkitRTCPeerConnection) {
                    window.webkitRTCPeerConnection = function() { return {}; };
                }
                if (window.mozRTCPeerConnection) {
                    window.mozRTCPeerConnection = function() { return {}; };
                }
            """)
            logging.info(" WebRTC IP leak protection enabled")
        except Exception:
            pass
        
        try:
            # Step 0: Verify proxy is working by checking our IP
            sb.open("https://api.ipify.org?format=json")
            time.sleep(2)
            try:
                ip_text = sb.get_text("body")
                logging.info(f" Browser IP check: {ip_text}")
                if proxy and proxy['ip'] not in ip_text:
                    logging.warning(f" WARNING: Browser IP does not match proxy {proxy['ip']}:{proxy['port']}!")
                    logging.warning(f" Proxy may not be working correctly!")
            except Exception:
                pass
            
            # Step 1: Navigate with smart anti-detection + DataDome solving
            url = "https://ticketsale.wimbledon.com/content"
            if not browser_manager.smart_navigate(sb, url, proxy=proxy):
                raise RuntimeError("Failed to navigate to site")
            
            # Step 2: Handle captcha using SeleniumBase methods
            if not bypass_captcha_sb(sb, proxy=proxy):
                raise RuntimeError("Failed to solve captcha after all attempts")

            # Step 2b: DataDome can appear after solving the numbers captcha
            time.sleep(2)
            if detect_and_solve_datadome(sb, proxy=proxy):
                logging.info(" DataDome solved after numbers captcha")
            
            # Step 3: Handle login process (with reCAPTCHA solving)
            if not handle_login_sb(sb, proxy=proxy):
                raise RuntimeError("Login process failed")

            # Step 4: Save the resulting page HTML
            save_page_html_sb(sb)
            
            # Step 5: Extended session stabilization
            logging.info(" Allowing extended time for all security systems to initialize...")
            logging.info("   This includes DataDome, bot management, and session tokens...")
            time.sleep(30)
            logging.info(" Extended session stabilization period completed")
            
            # Step 6: Create session and start monitoring
            session, headers = create_session_from_sb(sb)

            if not session or not headers:
                raise RuntimeError("Failed to create session for monitoring")
                
            # Step 7: Send test webhook and start monitoring
            logging.info("\n" + "=" * 50)
            logging.info(" Setup complete! Undetectable session established.")
            logging.info("=" * 50)

            logging.info("\nSending test Discord notification...")
            send_test_webhook()

            logging.info("\nStarting monitoring automatically...")
            start_monitoring(session, headers, sb, proxy=proxy)

        except DataDomeBanError:
            logging.info(" Ban detected, killing browser immediately...")
            raise
        except Exception as e:
            logging.error(f"Error in main execution: {e}")
            raise

        finally:
            logging.info("\nClosing browser...")
            # SeleniumBase automatically handles browser cleanup


if __name__ == "__main__":
    max_restarts = 20
    for restart_num in range(max_restarts):
        try:
            if restart_num > 0:
                logging.info(f"\n Restart #{restart_num} - picking new proxy...")
                time.sleep(5)
            main()
            break  # Clean exit
        except DataDomeBanError:
            logging.info(f" DataDome ban on attempt {restart_num + 1}/{max_restarts}, restarting...")
            continue
        except KeyboardInterrupt:
            logging.info("\n Stopped by user.")
            break
        except Exception as e:
            logging.error(f" Unexpected error on attempt {restart_num + 1}/{max_restarts}: {e}")
            logging.info(" Restarting entire process...")
            continue
    else:
        logging.error(f" Exhausted all {max_restarts} restart attempts")
