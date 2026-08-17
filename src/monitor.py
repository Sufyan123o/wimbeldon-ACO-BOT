"""
Wimbledon ticket availability monitor.

Polls the Secutix catalog API every few seconds and detects when a
performance's ``availability`` field transitions away from ``NONE``
(a restock event).  On each poll the full JSON response is also diffed
against the previous response so that any change in the API payload -
not just availability fields - is logged immediately.

A DataDome 403 response raises :exc:`DataDomeBanError`, which propagates
up to the main loop to trigger a proxy rotation and browser restart.
"""

import json
import logging
import time
import traceback
from datetime import datetime

import requests

from .notifications import send_datadome_ban_notification, send_restock_notification

CATALOG_PATH = (
    "/tnwr/v1/secure/catalog"
    "?maxPerformances=50&maxTimeslots=50&maxPerformanceDays=3&maxTimeslotDays=3&includeMetadata=true"
)

CATALOG_URL = f"https://ticketsale.wimbledon.com{CATALOG_PATH}"

# Fields whose values toggle on every Secutix maintenance cycle (~15 min).
# Diffs on these paths are suppressed to reduce noise.
_MAINTENANCE_NOISE = {
    "lowPrice", "highPrice", "salesEndDate", "withAdvantage",
    "advantages[len]", "advantages[0]", "advantages[1]",
    "advantages[2]", "advantages[3]",
}


class DataDomeBanError(Exception):
    """Raised when the API returns a 403, indicating a DataDome ban."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _log_failed_request(
    status: int,
    status_text: str = "",
    response_headers: dict | str = "",
    response_body: str = "",
    url: str = "",
    source: str = "",
    request_headers: dict | None = None,
) -> None:
    """Append a failed request record to ``failed_requests.log`` for analysis."""
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "url": url,
        "status": status,
        "status_text": status_text,
        "response_headers": response_headers,
        "response_body": response_body,
    }
    if request_headers:
        entry["request_headers"] = request_headers

    try:
        with open("failed_requests.log", "a", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(json.dumps(entry, indent=2, default=str) + "\n")
        logging.info(f"Failed request logged (HTTP {status}).")
    except Exception:
        pass


def _find_diffs(old: object, new: object, path: str = "") -> list[tuple[str, object, object]]:
    """Recursively diff two JSON-compatible structures.

    Returns a list of ``(path, old_value, new_value)`` tuples for every
    difference found.
    """
    diffs: list[tuple] = []

    if type(old) is not type(new):
        return [(path, old, new)]

    if isinstance(old, dict):
        for key in set(list(old) + list(new)):
            child = f"{path}.{key}" if path else key
            if key not in old:
                diffs.append((child, "<missing>", new[key]))
            elif key not in new:
                diffs.append((child, old[key], "<removed>"))
            else:
                diffs.extend(_find_diffs(old[key], new[key], child))

    elif isinstance(old, list):
        if len(old) != len(new):
            diffs.append((f"{path}[len]", len(old), len(new)))
        for i in range(min(len(old), len(new))):
            diffs.extend(_find_diffs(old[i], new[i], f"{path}[{i}]"))
        for i in range(len(old), len(new)):
            diffs.append((f"{path}[{i}]", "<missing>", new[i]))
        for i in range(len(new), len(old)):
            diffs.append((f"{path}[{i}]", old[i], "<removed>"))
    else:
        if old != new:
            diffs.append((path, old, new))

    return diffs


def _is_noise(path: str) -> bool:
    return any(k in path for k in _MAINTENANCE_NOISE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def monitor_performances(
    session: requests.Session,
    headers: dict,
    known_states: dict | None = None,
    sb=None,
    proxy: dict | None = None,
    last_response: dict | None = None,
) -> tuple[dict, dict | None]:
    """Poll the catalog API once and process any availability changes.

    When *sb* is provided the request is made via an in-browser ``fetch``
    call so that all session cookies and DataDome tokens are sent
    automatically.  Otherwise a ``requests.Session`` is used.

    Args:
        session:       Authenticated ``requests.Session``.
        headers:       HTTP headers to include with requests-based calls.
        known_states:  Dict mapping performance IDs to their last known
                       availability string.  Mutated in-place and returned.
        sb:            Optional SeleniumBase ``SB`` context for in-browser API
                       calls.
        proxy:         Proxy dict used to report DataDome bans.
        last_response: Full JSON response from the previous poll, used for
                       deep diffing.

    Returns:
        ``(known_states, last_response)`` - the updated state dict and the
        current API response (to pass as *last_response* on the next call).

    Raises:
        DataDomeBanError: When the API returns HTTP 403.
    """
    if known_states is None:
        known_states = {}

    data: dict | None = None

    try:
        # -- Fetch via in-browser XHR ------------------------------------------
        if sb:
            try:
                current_url = sb.get_current_url()
                if "ticketsale.wimbledon.com" not in current_url:
                    logging.info("Browser navigated away - returning to content page...")
                    sb.open("https://ticketsale.wimbledon.com/secured/content")
                    time.sleep(3)
            except Exception:
                pass

            fetch_result = sb.execute_async_script(f"""
                var callback = arguments[arguments.length - 1];
                fetch('{CATALOG_PATH}', {{
                    method: 'GET',
                    headers: {{
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'en',
                        'x-api-key': '344152a6-fa57-4e09-951f-96b8a38927d9',
                        'x-secutix-host': 'ticketsale.wimbledon.com'
                    }},
                    credentials: 'include'
                }}).then(function(r) {{
                    if (r.ok) return r.text().then(function(t) {{ callback(t); }});
                    return r.text().then(function(body) {{
                        callback(JSON.stringify({{
                            error: true, status: r.status,
                            statusText: r.statusText,
                            responseBody: body.substring(0, 5000)
                        }}));
                    }});
                }}).catch(function(e) {{
                    callback(JSON.stringify({{error: true, status: 0, statusText: e.message}}));
                }});
            """)

            if not fetch_result:
                logging.warning("Browser fetch returned empty result.")
                return known_states, last_response

            parsed = json.loads(fetch_result)
            if isinstance(parsed, dict) and parsed.get("error"):
                status = parsed.get("status", "unknown")
                logging.error(f"API error: HTTP {status}")
                _log_failed_request(
                    status=status,
                    status_text=parsed.get("statusText", ""),
                    response_body=parsed.get("responseBody", ""),
                    url=CATALOG_URL,
                    source="browser_xhr",
                )
                if status == 403:
                    send_datadome_ban_notification(proxy)
                    raise DataDomeBanError("DataDome 403 ban detected.")
                return known_states, last_response

            data = parsed

        # -- Fetch via requests.Session ----------------------------------------
        else:
            response = session.get(CATALOG_URL, headers=headers, verify=False, timeout=30)
            if response.status_code != 200:
                logging.error(f"API error: HTTP {response.status_code}")
                _log_failed_request(
                    status=response.status_code,
                    status_text=response.reason,
                    response_headers=dict(response.headers),
                    response_body=response.text[:5000],
                    url=str(response.url),
                    source="requests_session",
                    request_headers=dict(response.request.headers),
                )
                return known_states, last_response
            data = response.json()

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # -- Full-response diff -------------------------------------------------
        if last_response is None:
            perf_count = sum(
                len(item.get("product", {}).get("performances", []))
                for section in data.get("sections", [])
                for cluster in section.get("clusters", [])
                for item in cluster.get("items", [])
            )
            logging.info(
                f"[{current_time}] Baseline captured. "
                f"Monitoring {perf_count} performances for changes..."
            )
        else:
            diffs = _find_diffs(last_response, data)
            meaningful = [d for d in diffs if not _is_noise(d[0])]
            noise_count = len(diffs) - len(meaningful)

            if meaningful:
                logging.info(
                    f"[{current_time}] Response changed: "
                    f"{len(meaningful)} meaningful + {noise_count} maintenance diff(s)"
                )
                for path, old_val, new_val in meaningful[:20]:
                    logging.info(f"  {path}: {str(old_val)[:80]} -> {str(new_val)[:80]}")
                if len(meaningful) > 20:
                    logging.info(f"  ... and {len(meaningful) - 20} more changes.")
            elif diffs:
                logging.debug(f"[{current_time}] Maintenance cycle ({noise_count} toggled fields).")
            else:
                logging.info(f"[{current_time}] No changes.")

        # -- Availability / restock detection -----------------------------------
        for section in data.get("sections", []):
            for cluster in section.get("clusters", []):
                for item in cluster.get("items", []):
                    for performance in item.get("product", {}).get("performances", []):
                        name = performance.get("name", {}).get("en", "")
                        perf_id = str(performance.get("performanceId", ""))
                        buy_link = performance.get("action", {}).get("buy", "")
                        current_avail = performance.get("availability", "NONE")
                        previous_avail = known_states.get(perf_id, "NONE")

                        if previous_avail == "NONE" and current_avail != "NONE":
                            logging.info(f"RESTOCK DETECTED: {name}")
                            logging.info(f"  Availability: {previous_avail} -> {current_avail}")
                            logging.info(f"  Buy link: {buy_link}")
                            send_restock_notification(name, buy_link, name)

                        elif previous_avail != "NONE" and current_avail == "NONE" and perf_id in known_states:
                            logging.info(f"SOLD OUT: {name} ({previous_avail} -> {current_avail})")

                        known_states[perf_id] = current_avail

        return known_states, data

    except DataDomeBanError:
        raise
    except Exception as exc:
        logging.error(f"Error during monitoring poll: {exc}")
        traceback.print_exc()
        return known_states, last_response


def start_monitoring(
    session: requests.Session,
    headers: dict,
    sb=None,
    proxy: dict | None = None,
    poll_interval: float = 5.0,
) -> None:
    """Run the monitoring loop until interrupted or a DataDome ban occurs.

    Args:
        session:       Authenticated ``requests.Session``.
        headers:       HTTP headers dict.
        sb:            Optional SeleniumBase ``SB`` context for in-browser XHR.
        proxy:         Current proxy dict (for ban reporting).
        poll_interval: Seconds between each catalog poll.

    Raises:
        DataDomeBanError: Propagated to the caller to trigger a proxy restart.
    """
    logging.info("Starting ticket availability monitoring...")
    if sb:
        logging.info("In-browser XHR mode active.")
    logging.info(f"Poll interval: {poll_interval}s.  Press Ctrl+C to stop.")

    known_states: dict = {}
    last_response: dict | None = None

    try:
        while True:
            known_states, last_response = monitor_performances(
                session, headers,
                known_states=known_states,
                sb=sb,
                proxy=proxy,
                last_response=last_response,
            )
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logging.info("Monitoring stopped by user.")
    except DataDomeBanError:
        raise
    except Exception as exc:
        logging.error(f"Monitoring loop error: {exc}")
