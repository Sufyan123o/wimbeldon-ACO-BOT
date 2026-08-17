"""
Undetectable Chrome browser management via SeleniumBase.

``UndetectableBrowser`` wraps SeleniumBase's undetected-Chrome mode and
handles:

* ChromeDriver auto-installation
* Dynamic user-agent selection via ``UserAgent``
* Proxy injection (with or without authentication)
* DataDome-aware navigation (auto-solve on each page load)
"""

import json
import logging
import os
import secrets
import tempfile
import urllib.error

import chromedriver_autoinstaller
import requests
from bs4 import BeautifulSoup

from .captcha import CAPSOLVER_USER_AGENT, detect_and_solve_datadome
from .proxies import load_proxies, pick_proxy


class UserAgent:
    """Fetches and caches a pool of real-world Chrome user-agent strings.

    Falls back to a built-in list when the live source is unreachable.
    """

    _FALLBACK = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]

    def get_user_agents(self) -> list[str]:
        """Return a list of fresh user-agent strings, or the fallback list."""
        seed_agent = secrets.choice(self._FALLBACK)
        try:
            response = requests.get(
                "https://www.useragents.me",
                headers={"User-Agent": seed_agent},
                timeout=5,
            )
            soup = BeautifulSoup(response.content, "html.parser")
            agents = [
                ta.string.strip()
                for ta in soup.find_all("textarea", class_="form-control")[:25]
                if ta.string
            ]
            if agents:
                logging.info(f"Loaded {len(agents)} fresh user-agent strings.")
                return agents
        except Exception as exc:
            logging.warning(f"Could not fetch fresh user agents: {exc}")

        logging.info("Using fallback user-agent list.")
        return self._FALLBACK


class UndetectableBrowser:
    """Manages an undetectable SeleniumBase Chrome instance.

    Usage::

        browser = UndetectableBrowser()
        config  = browser.create_undetectable_browser()
        proxy   = pick_proxy(load_proxies())
        config  = browser.apply_proxy(config, proxy)

        with SB(**config) as sb:
            browser.smart_navigate(sb, "https://ticketsale.wimbledon.com/content", proxy)
    """

    def __init__(self) -> None:
        self._user_agents = UserAgent()

    # ------------------------------------------------------------------
    # Browser configuration
    # ------------------------------------------------------------------

    def _install_chromedriver(self) -> None:
        try:
            chromedriver_autoinstaller.install()
        except urllib.error.URLError as exc:
            logging.error(f"ChromeDriver installation failed: {exc}")
            raise

    def create_undetectable_browser(self) -> dict:
        """Build and return the SeleniumBase keyword-argument dict.

        The returned dict is passed directly to ``SB(**config)``.
        """
        self._install_chromedriver()

        logging.info(f"Using CapSolver-compatible user-agent: {CAPSOLVER_USER_AGENT[:80]}...")

        return {
            "uc": True,
            "headless": False,
            "incognito": False,
            "user_data_dir": None,
            "agent": CAPSOLVER_USER_AGENT,
            "chromium_arg": (
                "--webrtc-ip-handling-policy=disable_non_proxied_udp,"
                "--disable-webrtc-hw-decoding,"
                "--disable-webrtc-hw-encoding,"
                "--disable-features=WebRtcHideLocalIpsWithMdns"
            ),
        }

    # ------------------------------------------------------------------
    # Proxy injection
    # ------------------------------------------------------------------

    def apply_proxy(self, browser_config: dict, proxy: dict | None) -> dict:
        """Attach proxy settings to *browser_config* and return it."""
        if proxy is None:
            return browser_config

        if proxy["user"] and proxy["pass"]:
            ext_dir = self._create_proxy_auth_extension(proxy)
            browser_config["extension_dir"] = ext_dir
        else:
            browser_config["proxy"] = f"{proxy['ip']}:{proxy['port']}"

        logging.info(f"Proxy configured: {proxy['ip']}:{proxy['port']}")
        return browser_config

    @staticmethod
    def _create_proxy_auth_extension(proxy: dict) -> str:
        """Write a Chrome extension that auto-fills proxy auth credentials.

        Returns the path to the extension directory.
        """
        ext_dir = os.path.join(tempfile.gettempdir(), "wimbledon_proxy_auth_ext")
        os.makedirs(ext_dir, exist_ok=True)

        manifest = {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Proxy Auth",
            "permissions": [
                "proxy", "tabs", "unlimitedStorage", "storage",
                "<all_urls>", "webRequest", "webRequestBlocking", "privacy",
            ],
            "background": {"scripts": ["background.js"]},
            "minimum_chrome_version": "22.0.0",
        }

        background_js = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: { scheme: "http", host: "%s", port: parseInt(%s) },
        bypassList: ["localhost"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});

chrome.webRequest.onAuthRequired.addListener(
    function(details) {
        return { authCredentials: { username: "%s", password: "%s" } };
    },
    {urls: ["<all_urls>"]},
    ["blocking"]
);

try {
    if (chrome.privacy && chrome.privacy.network) {
        chrome.privacy.network.webRTCIPHandlingPolicy.set({
            value: "disable_non_proxied_udp"
        });
    }
} catch(e) {}
""" % (proxy["ip"], proxy["port"], proxy["user"], proxy["pass"])

        with open(os.path.join(ext_dir, "manifest.json"), "w") as f:
            json.dump(manifest, f)
        with open(os.path.join(ext_dir, "background.js"), "w") as f:
            f.write(background_js)

        logging.info(f"Proxy auth extension written to {ext_dir}")
        return ext_dir

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def smart_navigate(
        self,
        sb,
        url: str,
        proxy: dict | None = None,
        wait_time: float = 5.0,
        max_dd_retries: int = 3,
    ) -> bool:
        """Navigate to *url* and automatically resolve any DataDome challenge.

        Args:
            sb:             Active SeleniumBase ``SB`` context.
            url:            Target URL.
            proxy:          Proxy dict used by the DataDome solver.
            wait_time:      Seconds to wait after the page loads.
            max_dd_retries: Maximum DataDome solve attempts per navigation.

        Returns:
            ``True`` on success, ``False`` if all DataDome attempts fail.
        """
        try:
            sb.uc_open_with_reconnect(url, reconnect_time=4)
            logging.info(f"Navigated to: {url}")
        except Exception as exc:
            logging.error(f"Navigation error: {exc}")
            return False

        import time
        time.sleep(wait_time)

        for attempt in range(1, max_dd_retries + 1):
            if detect_and_solve_datadome(sb, proxy):
                logging.info("DataDome check passed.")
                return True
            logging.warning(f"DataDome solve attempt {attempt}/{max_dd_retries} failed.")
            if attempt < max_dd_retries:
                time.sleep(2)

        logging.error("All DataDome solve attempts exhausted.")
        return False
