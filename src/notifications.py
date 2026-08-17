"""
Discord webhook notifications.

Four notification types are supported:

* **Restock alert** - sent immediately when ticket availability changes from
  ``NONE`` to any available state.
* **Success** - sent after tickets are successfully added to the cart.
* **Failure** - sent when the automated add-to-cart attempt fails.
* **DataDome ban** - sent to the operational log channel when a 403 ban is
  detected and the bot is restarting.

Required environment variables
--------------------------------
DISCORD_WEBHOOK_URL
    Restock alert channel webhook.
DISCORD_LOG_WEBHOOK_URL  (optional, can also be hard-coded below)
    Operational / log channel webhook.
"""

import logging
import os
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_LOG_WEBHOOK_URL: str = os.getenv("DISCORD_LOG_WEBHOOK_URL", "")


def _post(webhook_url: str, payload: dict) -> bool:
    """POST *payload* to *webhook_url*.

    Returns ``True`` on HTTP 204 (Discord's success response).
    """
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        return response.status_code == 204
    except Exception as exc:
        logging.error(f"Webhook POST failed: {exc}")
        return False


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%d. %m. %Y %H:%M:%S")


# ---------------------------------------------------------------------------
# Operational / log notifications
# ---------------------------------------------------------------------------

def send_datadome_ban_notification(proxy: dict | None = None) -> None:
    """Notify the log channel that DataDome issued a 403 ban.

    Args:
        proxy: The proxy that was in use when the ban was detected.
    """
    if not DISCORD_LOG_WEBHOOK_URL:
        return
    proxy_info = f"{proxy['ip']}:{proxy['port']}" if proxy else "direct connection"
    payload = {
        "content": (
            f"**DataDome Ban Detected**\n"
            f"Proxy: `{proxy_info}`\n"
            f"Restarting with a new proxy..."
        )
    }
    _post(DISCORD_LOG_WEBHOOK_URL, payload)


def send_startup_notification() -> None:
    """Send a startup/test message to the log channel to verify connectivity."""
    if not DISCORD_LOG_WEBHOOK_URL:
        logging.warning("DISCORD_LOG_WEBHOOK_URL not configured - skipping startup ping.")
        return

    payload = {
        "embeds": [{
            "title": "Wimbledon Monitor - Started",
            "description": "Bot is online and monitoring for ticket availability.",
            "color": 0x0099FF,
            "fields": [
                {"name": "Status", "value": "Running", "inline": True},
            ],
            "footer": {"text": f"Wimbledon Ticket Monitor - {_timestamp()}"},
        }]
    }
    if _post(DISCORD_LOG_WEBHOOK_URL, payload):
        logging.info("Startup notification sent.")
    else:
        logging.warning("Failed to send startup notification.")


# ---------------------------------------------------------------------------
# Restock / purchase notifications
# ---------------------------------------------------------------------------

def send_restock_notification(day_name: str, buy_link: str, advantage_name: str) -> bool:
    """Send a restock alert to the main webhook channel.

    Args:
        day_name:       Human-readable event name (e.g. ``"Centre Court Day 1"``).
        buy_link:       Direct link to the purchase page.
        advantage_name: Ticket category / advantage name.

    Returns:
        ``True`` if the webhook was accepted (HTTP 204).
    """
    if not DISCORD_WEBHOOK_URL:
        logging.warning("DISCORD_WEBHOOK_URL not set - cannot send restock alert.")
        return False

    payload = {
        "embeds": [{
            "title": "Wimbledon Tickets Available",
            "description": f"[**{day_name}** tickets are now available!]({buy_link})",
            "color": 0x00FF00,
            "fields": [
                {"name": "Event",          "value": day_name,       "inline": True},
                {"name": "Advantage Type", "value": advantage_name, "inline": True},
                {"name": "Purchase Link",  "value": f"[Buy Tickets]({buy_link})", "inline": False},
            ],
            "footer": {"text": f"Wimbledon Ticket Monitor - {_timestamp()}"},
        }]
    }

    success = _post(DISCORD_WEBHOOK_URL, payload)
    if success:
        logging.info(f"Restock alert sent for: {day_name}")
    else:
        logging.error(f"Failed to send restock alert for: {day_name}")
    return success


def send_purchase_success(day_name: str, advantage_name: str, elapsed: float | None = None) -> bool:
    """Notify that tickets were successfully added to the cart.

    Args:
        day_name:       Event name.
        advantage_name: Ticket category.
        elapsed:        Seconds from restock detection to cart confirmation.
    """
    if not DISCORD_WEBHOOK_URL:
        return False

    timing_field = []
    if elapsed is not None:
        timing_field = [{"name": "Speed", "value": f"{elapsed:.3f}s from detection to cart", "inline": False}]

    payload = {
        "content": "@here",
        "embeds": [{
            "title": "Tickets Added to Cart",
            "description": f"Successfully added **{day_name}** tickets to cart.",
            "color": 0x00FF00,
            "fields": [
                {"name": "Event",          "value": day_name,       "inline": True},
                {"name": "Advantage Type", "value": advantage_name, "inline": True},
                {"name": "Status",         "value": "Confirmed in cart - proceed to checkout!", "inline": False},
            ] + timing_field,
            "footer": {"text": f"Wimbledon Automation - SUCCESS - {_timestamp()}"},
        }]
    }
    return _post(DISCORD_WEBHOOK_URL, payload)


def send_purchase_failure(day_name: str, advantage_name: str, elapsed: float | None = None) -> bool:
    """Notify that the automated purchase attempt failed.

    Args:
        day_name:       Event name.
        advantage_name: Ticket category.
        elapsed:        Seconds spent attempting the purchase.
    """
    if not DISCORD_WEBHOOK_URL:
        return False

    timing_field = []
    if elapsed is not None:
        timing_field = [{"name": "Time Spent", "value": f"{elapsed:.3f}s attempting purchase", "inline": False}]

    payload = {
        "embeds": [{
            "title": "Automated Purchase Failed",
            "description": f"Purchase attempt failed for **{day_name}**.",
            "color": 0xFF0000,
            "fields": [
                {"name": "Event",          "value": day_name,       "inline": True},
                {"name": "Advantage Type", "value": advantage_name, "inline": True},
                {"name": "Reason",         "value": "Tickets sold out or insufficient stock during checkout.", "inline": False},
            ] + timing_field,
            "footer": {"text": f"Wimbledon Automation - {_timestamp()}"},
        }]
    }
    return _post(DISCORD_WEBHOOK_URL, payload)
