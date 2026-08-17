"""
Proxy management utilities.

Proxies are read from the PROXIES environment variable.  Each line must follow
one of two formats::

    IP:PORT
    IP:PORT:USERNAME:PASSWORD

A random proxy is selected for each browser session to rotate identities and
reduce the risk of DataDome / Akamai bans.
"""

import logging
import os
import secrets


def load_proxies() -> list[dict]:
    """Load and parse proxies from the ``PROXIES`` environment variable.

    Returns a list of proxy dicts with keys ``ip``, ``port``, ``user``,
    ``pass``.  Returns an empty list when no proxies are configured.
    """
    raw = os.getenv("PROXIES", "").strip()
    if not raw:
        logging.info("No proxies configured (set PROXIES in .env)")
        return []

    proxies: list[dict] = []
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


def pick_proxy(proxy_list: list[dict]) -> dict | None:
    """Return a random proxy from *proxy_list*, or ``None`` if the list is empty."""
    if not proxy_list:
        return None
    return secrets.choice(proxy_list)


def get_proxy_string(proxy: dict | None) -> str | None:
    """Convert a proxy dict to CapSolver proxy format ``IP:PORT:USER:PASS``.

    Returns ``None`` when *proxy* is ``None``.
    """
    if not proxy:
        return None
    if proxy["user"] and proxy["pass"]:
        return f"{proxy['ip']}:{proxy['port']}:{proxy['user']}:{proxy['pass']}"
    return f"{proxy['ip']}:{proxy['port']}"
