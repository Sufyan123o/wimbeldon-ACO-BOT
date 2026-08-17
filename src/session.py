"""
Session extraction from Selenium / SeleniumBase browser instances.

After a successful login the browser holds all the anti-bot cookies
(``datadome``, ``STX_SESSION``, ``stx_contact_*``, Akamai tokens, etc.).
These functions transfer those cookies into a ``requests.Session`` so that
the monitoring loop can poll the ticket API with the authenticated identity.

Two variants are provided:
* :func:`create_session_from_sb` - for the SeleniumBase ``SB`` context (primary).
* :func:`create_session_from_driver` - for the legacy ``selenium.webdriver`` driver.
"""

import logging
import time

import requests
from dotenv import load_dotenv

from .proxies import load_proxies, pick_proxy

load_dotenv()

WIMBLEDON_CONTENT_URL = "https://ticketsale.wimbledon.com/content"
WIMBLEDON_SECURED_URL = "https://ticketsale.wimbledon.com/secured/content"

# Fallback API key used when the browser-extracted value is unavailable
_FALLBACK_API_KEY = "344152a6-fa57-4e09-951f-96b8a38927d9"

_CRITICAL_COOKIES = {
    "STX_SESSION": "Secutix session ID (required for API calls)",
    "stx_contact_AELTC_B2C_v1": "Contact session (required for authentication)",
    "datadome": "DataDome anti-bot cookie",
    "bm_sv": "Akamai bot management",
    "ak_bmsc": "Akamai bot manager script",
}


def _build_session_with_proxy() -> requests.Session:
    """Create a ``requests.Session`` pre-configured with a random proxy."""
    session = requests.Session()
    proxy_list = load_proxies()
    proxy = pick_proxy(proxy_list)
    if proxy:
        if proxy["user"] and proxy["pass"]:
            proxy_url = f"http://{proxy['user']}:{proxy['pass']}@{proxy['ip']}:{proxy['port']}"
        else:
            proxy_url = f"http://{proxy['ip']}:{proxy['port']}"
        session.proxies = {"http": proxy_url, "https": proxy_url}
        logging.info(f"Requests session proxy: {proxy['ip']}:{proxy['port']}")
    return session


def _log_critical_cookie_status(session: requests.Session) -> None:
    """Log the presence / absence of security-critical cookies."""
    session_cookies_found = 0
    missing = []

    for name, description in _CRITICAL_COOKIES.items():
        value = session.cookies.get(name)
        if value:
            logging.info(f"  Cookie present: {name} ({description})")
            if name in ("STX_SESSION", "stx_contact_AELTC_B2C_v1"):
                session_cookies_found += 1
        else:
            logging.warning(f"  Cookie MISSING: {name} ({description})")
            missing.append(name)

    if session_cookies_found < 2:
        logging.error(
            f"Missing {2 - session_cookies_found}/2 required session cookies.  "
            "API calls will likely return 403.  Ensure login completed successfully."
        )


def create_session_from_sb(sb) -> tuple[requests.Session | None, dict | None]:
    """Extract the authenticated browser session from a SeleniumBase context.

    Navigates to the content page if necessary, copies all cookies, attempts
    to read the ``x-api-key`` from the page, then returns a ready-to-use
    ``(session, headers)`` tuple.

    Args:
        sb: Active SeleniumBase ``SB`` context after a successful login.

    Returns:
        ``(session, headers)`` on success, or ``(None, None)`` on failure.
    """
    logging.info("Extracting session cookies from SeleniumBase browser...")
    try:
        current_url = sb.get_current_url()
        if "ticketsale.wimbledon.com" not in current_url:
            logging.info("Navigating to content page to capture cookies...")
            sb.open(WIMBLEDON_CONTENT_URL)
            time.sleep(3)

        cookies = sb.driver.get_cookies()
        session = _build_session_with_proxy()

        for cookie in cookies:
            session.cookies.set(cookie["name"], cookie["value"])

        _log_critical_cookie_status(session)

        user_agent = sb.execute_script("return navigator.userAgent;")

        # Attempt to read the API key injected by the React app
        x_api_key = sb.execute_script("""
            var meta = document.querySelector('meta[name="x-api-key"]');
            if (meta) return meta.content;
            if (window.__CONFIG__ && window.__CONFIG__.apiKey) return window.__CONFIG__.apiKey;
            if (window.__NEXT_DATA__) {
                var m = JSON.stringify(window.__NEXT_DATA__).match(
                    /x-api-key["']\\s*:\\s*["']([^"']+)/
                );
                if (m) return m[1];
            }
            return '';
        """) or ""

        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en",
            "Referer": WIMBLEDON_SECURED_URL,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-api-key": x_api_key or _FALLBACK_API_KEY,
            "X-Secutix-Host": "ticketsale.wimbledon.com",
        }

        if not x_api_key:
            logging.info("x-api-key not found in page - using fallback value.")

        session.headers.update(headers)
        logging.info(f"Session ready.  Cookies extracted: {len(cookies)}")
        return session, headers

    except Exception as exc:
        logging.error(f"Failed to extract session from SeleniumBase: {exc}")
        return None, None


def create_session_from_driver(driver) -> tuple[requests.Session | None, dict | None]:
    """Extract the authenticated browser session from a legacy Selenium driver.

    Args:
        driver: A ``selenium.webdriver`` instance after a successful login.

    Returns:
        ``(session, headers)`` on success, or ``(None, None)`` on failure.
    """
    logging.info("Waiting for browser session to stabilise...")
    time.sleep(5)

    try:
        current_url = driver.current_url
        if "content" not in current_url:
            driver.get(WIMBLEDON_CONTENT_URL)
            time.sleep(3)

        cookies = driver.get_cookies()
        session = _build_session_with_proxy()

        for cookie in cookies:
            try:
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain", ".wimbledon.com"),
                    path=cookie.get("path", "/"),
                )
            except Exception as exc:
                logging.warning(f"Could not set cookie {cookie['name']}: {exc}")

        _log_critical_cookie_status(session)

        user_agent = driver.execute_script("return navigator.userAgent;")

        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en",
            "Referer": WIMBLEDON_SECURED_URL,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-api-key": _FALLBACK_API_KEY,
            "X-Secutix-Host": "ticketsale.wimbledon.com",
        }

        session.headers.update(headers)
        logging.info(f"Session ready.  Cookies extracted: {len(cookies)}")
        return session, headers

    except Exception as exc:
        logging.error(f"Failed to extract session from Selenium driver: {exc}")
        return None, None
