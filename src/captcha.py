"""
CapSolver-backed captcha solvers.

Supports three challenge types encountered on the Wimbledon ticket site:

* Image-to-text captcha (numeric, via ``ImageToTextTask``)
* DataDome slider/interstitial (via ``DatadomeSliderTask``)
* Google reCAPTCHA v2 (via ``ReCaptchaV2TaskProxyLess`` / ``ReCaptchaV2Task``)

All solvers follow the same pattern: create a task on the CapSolver API, then
poll until the solution is ready or the attempt times out.
"""

import base64
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv

from .proxies import get_proxy_string

load_dotenv()

API_KEY: str = os.getenv("API_KEY", "")

# User-agent used by the browser.  CapSolver's DataDome solver requires the
# user-agent to match exactly what the browser sends.
CAPSOLVER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

WIMBLEDON_URL = "https://ticketsale.wimbledon.com/content"
CAPSOLVER_CREATE = "https://api.capsolver.com/createTask"
CAPSOLVER_RESULT = "https://api.capsolver.com/getTaskResult"


# ---------------------------------------------------------------------------
# Image-to-text (numeric CAPTCHA)
# ---------------------------------------------------------------------------

def solve_image_captcha(base64_image: str) -> str | None:
    """Solve a base64-encoded numeric image captcha via CapSolver.

    Args:
        base64_image: Base64-encoded PNG screenshot of the captcha element.

    Returns:
        The recognised text, or ``None`` on failure.
    """
    payload = {
        "clientKey": API_KEY,
        "task": {
            "type": "ImageToTextTask",
            "module": "module_016",
            "body": base64_image,
        },
    }
    try:
        response = requests.post(CAPSOLVER_CREATE, json=payload, timeout=30)
        result = response.json()
    except Exception as exc:
        logging.error(f"Image captcha request error: {exc}")
        return None

    if result.get("errorId") == 0 and "solution" in result:
        solution = result["solution"]
        return solution.get("text") or solution.get("answers")

    logging.error(f"CapSolver image captcha error: {result}")
    return None


# ---------------------------------------------------------------------------
# DataDome slider / interstitial
# ---------------------------------------------------------------------------

def solve_datadome(captcha_url: str, proxy: dict, website_url: str = WIMBLEDON_URL) -> str | None:
    """Solve a DataDome challenge via CapSolver's ``DatadomeSliderTask``.

    Args:
        captcha_url: The full URL of the DataDome captcha delivery page.
        proxy:       Proxy dict as returned by :func:`proxies.pick_proxy`.
        website_url: The origin URL that triggered the DataDome challenge.

    Returns:
        The solved ``datadome`` cookie string, or ``None`` on failure.
    """
    proxy_str = get_proxy_string(proxy)
    if not proxy_str:
        logging.error("DataDome solver requires a proxy.")
        return None

    if "t=bv" in captcha_url:
        logging.error("DataDome: IP is hard-banned (t=bv).  Change your proxy.")
        return None

    logging.info("Solving DataDome challenge via CapSolver...")

    payload = {
        "clientKey": API_KEY,
        "task": {
            "type": "DatadomeSliderTask",
            "websiteURL": website_url,
            "captchaUrl": captcha_url,
            "userAgent": CAPSOLVER_USER_AGENT,
            "proxy": proxy_str,
        },
    }

    try:
        res = requests.post(CAPSOLVER_CREATE, json=payload, timeout=30)
        resp = res.json()
    except Exception as exc:
        logging.error(f"DataDome createTask request failed: {exc}")
        return None

    task_id = resp.get("taskId")
    if not task_id:
        logging.error(f"DataDome createTask failed: {resp}")
        return None

    logging.info(f"DataDome task {task_id} created - polling for result...")

    for _ in range(60):
        time.sleep(1)
        try:
            poll = requests.post(
                CAPSOLVER_RESULT,
                json={"clientKey": API_KEY, "taskId": task_id},
                timeout=30,
            ).json()
        except Exception as exc:
            logging.warning(f"Poll error: {exc}")
            continue

        status = poll.get("status")
        if status == "ready":
            cookie = poll.get("solution", {}).get("cookie")
            if cookie:
                logging.info("DataDome solved successfully.")
                return cookie
            logging.error(f"DataDome solution missing cookie: {poll}")
            return None
        if status == "failed" or poll.get("errorId"):
            logging.error(f"DataDome solve failed: {poll}")
            return None

    logging.error("DataDome solve timed out after 60 seconds.")
    return None


def _apply_datadome_cookie(sb, cookie_str: str) -> bool:
    """Inject a solved DataDome cookie into the browser and reload the page.

    Args:
        sb:          Active SeleniumBase ``SB`` context.
        cookie_str:  Raw cookie string returned by :func:`solve_datadome`.

    Returns:
        ``True`` if the page loaded successfully after injection.
    """
    try:
        cookie_value = cookie_str.split("datadome=")[1].split(";")[0]

        sb.execute_script(
            f"document.cookie = 'datadome={cookie_value}; path=/; secure; samesite=lax';"
        )
        sb.driver.add_cookie({
            "name": "datadome",
            "value": cookie_value,
            "domain": ".wimbledon.com",
            "path": "/",
            "secure": True,
            "sameSite": "Lax",
        })

        logging.info("DataDome cookie applied - reloading page...")
        sb.driver.refresh()
        time.sleep(3)

        new_url = sb.get_current_url()
        new_source = sb.get_page_source()
        if "geo.captcha-delivery.com" in new_url or "Access is temporarily restricted" in new_source:
            logging.error("DataDome cookie ineffective - still blocked.")
            return False

        logging.info("DataDome bypass successful.")
        return True

    except Exception as exc:
        logging.error(f"Error applying DataDome cookie: {exc}")
        return False


def detect_and_solve_datadome(sb, proxy: dict | None, max_retries: int = 3) -> bool:
    """Detect a DataDome challenge on the current page and solve it.

    Checks for three presentation modes: direct redirect, embedded iframe,
    and block-page with an extracted URL.

    Args:
        sb:          Active SeleniumBase ``SB`` context.
        proxy:       Proxy to pass to the CapSolver DataDome solver.
        max_retries: Number of solve attempts before giving up.

    Returns:
        ``True`` when no challenge is present or it was solved successfully.
    """
    try:
        current_url = sb.get_current_url()

        if "geo.captcha-delivery.com" in current_url:
            logging.info("DataDome captcha detected (direct redirect).")
            cookie = solve_datadome(current_url, proxy)
            return _apply_datadome_cookie(sb, cookie) if cookie else False

        captcha_url = sb.execute_script("""
            var iframes = document.querySelectorAll(
                'iframe[src*="captcha-delivery"], iframe[src*="datadome"]'
            );
            if (iframes.length > 0) return iframes[0].src;
            var ddEl = document.querySelector('#datadome, .datadome, [data-datadome]');
            if (ddEl) {
                var iframe = ddEl.querySelector('iframe');
                if (iframe) return iframe.src;
            }
            return null;
        """)

        if captcha_url:
            logging.info(f"DataDome iframe detected.")
            cookie = solve_datadome(captcha_url, proxy)
            return _apply_datadome_cookie(sb, cookie) if cookie else False

        page_source = sb.get_page_source()
        if "Access is temporarily restricted" in page_source or "captcha-delivery.com" in page_source:
            logging.info("DataDome block page detected - extracting captcha URL...")
            match = re.search(
                r"(https://geo\.captcha-delivery\.com/captcha/[^\"\'>\s]+)", page_source
            )
            if match:
                captcha_url = match.group(1)
                cookie = solve_datadome(captcha_url, proxy)
                return _apply_datadome_cookie(sb, cookie) if cookie else False
            logging.error("Could not extract DataDome captcha URL from page source.")
            return False

        return True  # No DataDome detected

    except Exception as exc:
        logging.error(f"Error during DataDome detection: {exc}")
        return True  # Do not block flow on detection failure


# ---------------------------------------------------------------------------
# reCAPTCHA v2
# ---------------------------------------------------------------------------

def solve_recaptcha_v2(website_url: str, website_key: str, proxy: dict | None = None) -> str | None:
    """Solve a reCAPTCHA v2 challenge via CapSolver and return the token.

    Args:
        website_url: Page URL where the reCAPTCHA is rendered.
        website_key: Site key extracted from the ``data-sitekey`` attribute.
        proxy:       Optional proxy dict for the proxied task variant.

    Returns:
        The ``gRecaptchaResponse`` token string, or ``None`` on failure.
    """
    logging.info(f"Solving reCAPTCHA v2 for {website_url}...")

    task: dict = {
        "type": "ReCaptchaV2TaskProxyLess",
        "websiteURL": website_url,
        "websiteKey": website_key,
    }

    if proxy:
        proxy_str = get_proxy_string(proxy)
        if proxy_str:
            task["type"] = "ReCaptchaV2Task"
            task["proxy"] = f"http:{proxy['ip']}:{proxy['port']}:{proxy['user']}:{proxy['pass']}"

    payload = {"clientKey": API_KEY, "task": task}

    try:
        res = requests.post(CAPSOLVER_CREATE, json=payload, timeout=30)
        resp = res.json()
    except Exception as exc:
        logging.error(f"reCAPTCHA createTask failed: {exc}")
        return None

    task_id = resp.get("taskId")
    if not task_id:
        logging.error(f"reCAPTCHA createTask error: {resp}")
        return None

    logging.info(f"reCAPTCHA task {task_id} created - polling...")

    for _ in range(120):
        time.sleep(1)
        try:
            poll = requests.post(
                CAPSOLVER_RESULT,
                json={"clientKey": API_KEY, "taskId": task_id},
                timeout=30,
            ).json()
        except Exception as exc:
            logging.warning(f"Poll error: {exc}")
            continue

        status = poll.get("status")
        if status == "ready":
            token = poll.get("solution", {}).get("gRecaptchaResponse")
            if token:
                logging.info("reCAPTCHA v2 solved successfully.")
                return token
            logging.error(f"reCAPTCHA solution missing token: {poll}")
            return None
        if status == "failed" or poll.get("errorId"):
            logging.error(f"reCAPTCHA solve failed: {poll}")
            return None

    logging.error("reCAPTCHA solve timed out after 120 seconds.")
    return None


def detect_and_solve_recaptcha(sb, proxy: dict | None = None) -> bool:
    """Detect a reCAPTCHA v2 on the current page, solve it, and inject the token.

    Args:
        sb:    Active SeleniumBase ``SB`` context.
        proxy: Optional proxy for the solver.

    Returns:
        ``True`` when no reCAPTCHA is present or it was solved successfully.
    """
    try:
        recaptcha_info = sb.execute_script("""
            var recaptchaFrame = document.querySelector(
                'iframe[src*="recaptcha"], iframe[title*="reCAPTCHA"]'
            );
            var recaptchaDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
            var hasGrecaptcha = typeof grecaptcha !== 'undefined';
            var sitekey = null;
            if (recaptchaDiv) sitekey = recaptchaDiv.getAttribute('data-sitekey');
            if (!sitekey && recaptchaFrame) {
                var m = recaptchaFrame.src.match(/[?&]k=([^&]+)/);
                if (m) sitekey = m[1];
            }
            return {
                found: !!(recaptchaFrame || recaptchaDiv || hasGrecaptcha),
                sitekey: sitekey
            };
        """)

        if not recaptcha_info or not recaptcha_info.get("found"):
            return True

        sitekey = recaptcha_info.get("sitekey")
        if not sitekey:
            logging.error("reCAPTCHA detected but sitekey could not be extracted.")
            return False

        token = solve_recaptcha_v2(sb.get_current_url(), sitekey, proxy)
        if not token:
            return False

        sb.execute_script("""
            var token = arguments[0];
            // Fill all g-recaptcha-response textareas
            document.querySelectorAll(
                'textarea[name="g-recaptcha-response"], #g-recaptcha-response'
            ).forEach(function(ta) { ta.innerHTML = token; ta.value = token; });

            // Fire data-callback
            var gcDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
            if (gcDiv) {
                var cbName = gcDiv.getAttribute('data-callback');
                if (cbName && typeof window[cbName] === 'function') window[cbName](token);
            }

            // Traverse ___grecaptcha_cfg clients
            if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                Object.keys(___grecaptcha_cfg.clients).forEach(function(k) {
                    (function find(obj, depth) {
                        if (depth > 8 || !obj || typeof obj !== 'object') return;
                        Object.keys(obj).forEach(function(p) {
                            try {
                                if (typeof obj[p] === 'function' &&
                                    (p === 'callback' || p === 'success-callback' || p === 'success')) {
                                    obj[p](token);
                                } else { find(obj[p], depth + 1); }
                            } catch(e) {}
                        });
                    })(___grecaptcha_cfg.clients[k], 0);
                });
            }

            // Override getResponse as fallback
            if (typeof grecaptcha !== 'undefined') {
                grecaptcha.getResponse = function() { return token; };
                if (grecaptcha.enterprise)
                    grecaptcha.enterprise.getResponse = function() { return token; };
            }
        """, token)

        logging.info("reCAPTCHA token injected successfully.")
        time.sleep(1)
        return True

    except Exception as exc:
        logging.error(f"Error during reCAPTCHA detection/solve: {exc}")
        return False
