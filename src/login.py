"""
Login automation for the Wimbledon ticket portal.

Handles two stages that appear in sequence:

1. **Image captcha** - a numeric challenge on the first page load, solved
   via CapSolver's ``ImageToTextTask``.
2. **Login form** - email + password, followed by a reCAPTCHA v2 that
   Gigya injects after the first submission attempt.

Both functions are designed for the SeleniumBase ``SB`` context manager.
"""

import logging
import os
import time

from .captcha import (
    detect_and_solve_datadome,
    detect_and_solve_recaptcha,
    solve_image_captcha,
    solve_recaptcha_v2,
)


def bypass_captcha(sb, proxy: dict | None = None) -> bool:
    """Solve the numeric image captcha on the Wimbledon entry page.

    Waits for the captcha image element, captures a screenshot, submits the
    solution via CapSolver, then verifies that the portal moves to the next
    step (waiting-room or login form).

    Args:
        sb:    Active SeleniumBase ``SB`` context.
        proxy: Proxy dict used for DataDome checks that may follow captcha
               submission.

    Returns:
        ``True`` if the captcha was bypassed, ``False`` after three failed
        attempts.
    """
    logging.info("Waiting for captcha image to load...")
    try:
        sb.wait_for_element_present("#img_captcha", timeout=10)
        logging.info("Captcha image found.")
    except Exception as exc:
        logging.error(f"Captcha image not found after 10 seconds: {exc}")
        return False

    try:
        import base64
        captcha_element = sb.find_element("#img_captcha")
        b64_data = base64.b64encode(captcha_element.screenshot_as_png).decode("utf-8")
        logging.info("Captcha screenshot captured.")
    except Exception as exc:
        logging.error(f"Error taking captcha screenshot: {exc}")
        return False

    result = solve_image_captcha(b64_data)
    if not result:
        logging.error("Failed to solve captcha.")
        return False

    bypassed = False
    for attempt in range(1, 4):
        logging.info(f"Captcha attempt {attempt}: submitting '{result}'")
        try:
            sb.clear("#secret")
            sb.type("#secret", str(result))
            sb.execute_script("submitCaptcha();")
            time.sleep(2)

            # Solve DataDome if it appeared after submission
            detect_and_solve_datadome(sb, proxy=proxy)
            time.sleep(2)

            if (
                not sb.is_element_present("#img_captcha")
                or sb.is_element_present("#actionButton")
                or sb.is_element_present("#loginID")
            ):
                bypassed = True
                logging.info("Captcha bypassed.")

                # Handle waiting room
                try:
                    if sb.is_element_visible("#actionButton"):
                        logging.info("Waiting room detected - waiting for Enter button...")
                        sb.wait_for_element_clickable("#actionButton", timeout=15)
                        sb.click("#actionButton")
                        logging.info("Enter button clicked. Proceeding to main site.")
                        time.sleep(2)
                except Exception as exc:
                    logging.debug(f"No waiting-room button: {exc}")
                break

        except Exception as exc:
            logging.error(f"Error during captcha submission: {exc}")

        if not bypassed and attempt < 3:
            logging.info("Captcha failed, retrying with new image...")
            try:
                import base64
                captcha_element = sb.find_element("#img_captcha")
                b64_data = base64.b64encode(captcha_element.screenshot_as_png).decode("utf-8")
                result = solve_image_captcha(b64_data)
                if not result:
                    break
            except Exception as exc:
                logging.error(f"Error refreshing captcha: {exc}")
                break

    return bypassed


def handle_login(sb, proxy: dict | None = None) -> bool:
    """Fill in the login form and solve the reCAPTCHA v2 that Gigya requires.

    The function presses Enter on the password field to trigger Gigya's
    reCAPTCHA requirement, solves it via CapSolver, injects the token, then
    submits the form again.

    Args:
        sb:    Active SeleniumBase ``SB`` context.
        proxy: Optional proxy passed to the reCAPTCHA solver.

    Returns:
        ``True`` when login completes (or appears to have succeeded), ``False``
        on missing credentials or repeated failures.
    """
    logging.info("Waiting for login form...")

    # Accept cookie banner if present
    try:
        for selector in [
            "button:contains('Accept All Cookies')",
            "#onetrust-accept-btn-handler",
            "button:contains('Accept')",
        ]:
            try:
                if sb.is_element_present(selector, timeout=3):
                    sb.click(selector)
                    logging.info("Cookie banner dismissed.")
                    time.sleep(1)
                    break
            except Exception:
                continue
    except Exception:
        logging.debug("No cookie banner detected.")

    try:
        sb.wait_for_element_visible("#loginID", timeout=20)
    except Exception as exc:
        logging.error(f"Login form not found: {exc}")
        return False

    if not sb.is_element_visible("#loginID") or not sb.is_element_visible("#password"):
        logging.info("Login form not visible - assuming already logged in.")
        return True

    email = os.getenv("LOGIN_EMAIL", "")
    password = os.getenv("LOGIN_PASSWORD", "")
    if not email or not password:
        logging.error("LOGIN_EMAIL / LOGIN_PASSWORD not set in environment.")
        return False

    sb.clear("#loginID")
    sb.type("#loginID", email)
    sb.clear("#password")
    sb.type("#password", password)
    logging.info("Credentials entered.")

    # Press Enter to trigger reCAPTCHA requirement
    login_already_succeeded = False
    captcha_required = False

    for attempt in range(1, 4):
        try:
            if not sb.is_element_present("#password"):
                logging.info("Password field gone - login already succeeded.")
                login_already_succeeded = True
                break
            sb.press_keys("#password", "\n")
        except Exception:
            logging.info("Password field vanished during submit - login succeeded.")
            login_already_succeeded = True
            break

        logging.info(f"Login press {attempt}...")

        for _ in range(10):
            time.sleep(1)
            try:
                if not sb.is_element_present("#password"):
                    login_already_succeeded = True
                    break
                current_url = sb.get_current_url()
                if "login" not in current_url.lower() and "mywimbledon" not in current_url.lower():
                    login_already_succeeded = True
                    break
            except Exception:
                login_already_succeeded = True
                break

        if login_already_succeeded:
            break

        page_text = sb.execute_script("""
            var errors = document.querySelectorAll(
                '.gigya-error-msg, .gigya-error-msg-active, [data-bound-to]'
            );
            var text = '';
            errors.forEach(function(e) { text += ' ' + e.textContent; });
            return text.toLowerCase();
        """) or ""

        if "captcha" in page_text or "recaptcha" in page_text:
            logging.info("Gigya reCAPTCHA requirement triggered.")
            captcha_required = True
            break

    if login_already_succeeded:
        logging.info("Waiting for session to initialise...")
        time.sleep(10)
        return True

    if not captcha_required:
        logging.warning("reCAPTCHA requirement not detected - attempting to continue.")

    # Solve the reCAPTCHA that Gigya injected
    recaptcha_info = sb.execute_script("""
        var div = document.querySelector('.g-recaptcha, [data-sitekey]');
        var sitekey = div ? div.getAttribute('data-sitekey') : null;
        if (!sitekey) {
            var frame = document.querySelector('iframe[src*="recaptcha"]');
            if (frame) { var m = frame.src.match(/[?&]k=([^&]+)/); if (m) sitekey = m[1]; }
        }
        return {found: !!sitekey, sitekey: sitekey};
    """)

    recaptcha_token = None
    if recaptcha_info and recaptcha_info.get("found"):
        sitekey = recaptcha_info.get("sitekey")
        logging.info(f"reCAPTCHA detected. Sitekey: {sitekey}")
        for attempt in range(1, 4):
            recaptcha_token = solve_recaptcha_v2(sb.get_current_url(), sitekey, proxy)
            if recaptcha_token:
                break
            logging.warning(f"reCAPTCHA solve attempt {attempt}/3 failed.")
            time.sleep(2)
    else:
        logging.info("No reCAPTCHA detected - login may have already succeeded.")

    if recaptcha_token:
        logging.info("Injecting reCAPTCHA token and submitting form...")
        sb.execute_script("""
            var token = arguments[0];
            document.querySelectorAll(
                'textarea[name="g-recaptcha-response"], #g-recaptcha-response'
            ).forEach(function(ta) { ta.innerHTML = token; ta.value = token; });

            var gcDiv = document.querySelector('.g-recaptcha, [data-sitekey]');
            if (gcDiv) {
                var ta = gcDiv.querySelector('textarea');
                if (ta) { ta.innerHTML = token; ta.value = token; }
                var cbName = gcDiv.getAttribute('data-callback');
                if (cbName && typeof window[cbName] === 'function') window[cbName](token);
            }

            if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                Object.keys(___grecaptcha_cfg.clients).forEach(function(k) {
                    (function find(obj, depth) {
                        if (depth > 8 || !obj || typeof obj !== 'object') return;
                        Object.keys(obj).forEach(function(p) {
                            try {
                                if (typeof obj[p] === 'function' &&
                                    (p === 'callback' || p === 'success-callback' || p === 'success'))
                                    obj[p](token);
                                else find(obj[p], depth + 1);
                            } catch(e) {}
                        });
                    })(___grecaptcha_cfg.clients[k], 0);
                });
            }

            if (typeof grecaptcha !== 'undefined') {
                grecaptcha.getResponse = function() { return token; };
                if (grecaptcha.enterprise)
                    grecaptcha.enterprise.getResponse = function() { return token; };
            }
        """, recaptcha_token)

        time.sleep(1)
        sb.press_keys("#password", "\n")
        logging.info("Form submitted with reCAPTCHA token.")

    logging.info("Waiting for session to initialise...")
    time.sleep(10)
    logging.info("Login sequence complete.")
    return True
