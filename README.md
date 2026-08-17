# Wimbledon Ticket Monitor

An automated bot that monitors the [Wimbledon ticket portal](https://ticketsale.wimbledon.com) for Centre Court ticket restocks and attempts to add them to cart the moment they become available.

## How It Works

The bot handles the full journey from a cold browser start to a confirmed cart:

1. **Anti-bot bypass** — Navigates to the ticket portal using an undetected Chrome instance (SeleniumBase UC mode) to avoid Cloudflare, DataDome, and browser fingerprinting checks.
2. **Captcha solving** — Solves the numeric image captcha on entry via [CapSolver](https://capsolver.com).
3. **Waiting room** — Auto-clicks the "Enter" button when a virtual queue is detected.
4. **Login** — Fills in credentials and solves the Gigya reCAPTCHA v2 that appears on first login attempt.
5. **Session extraction** — Copies all authenticated cookies (including `STX_SESSION`, `datadome`, Akamai tokens) into a `requests.Session` for high-frequency API polling.
6. **Monitoring** — Polls the `/tnwr/v1/secure/catalog` API every few seconds, diffs the full JSON response, and fires a Discord alert the instant availability changes from `NONE`.
7. **Automated checkout** — Navigates to the buy page, selects quantity 2, and clicks "Add to cart" in under a second.

---

## Project Structure

```
wimbledon-ticket-monitor/
├── test.py                  # Main bot (final working version, ~2 500 lines)
├── main.py                  # Progression v1  – Selenium + basic captcha + monitoring
├── request.py               # Progression v0  – raw HTTP request exploration
├── captest.py               # Utility: standalone CapSolver image captcha tester
├── test_undetectable.py     # Utility: validate undetected Chrome detection scores
│
└── src/                     # Refactored modular library (same logic, clean structure)
    ├── proxies.py            # Proxy loading, selection, formatting
    ├── captcha.py            # CapSolver – image captcha, DataDome, reCAPTCHA v2
    ├── browser.py            # UndetectableBrowser – SeleniumBase UC wrapper
    ├── login.py              # Captcha bypass + Gigya login automation
    ├── session.py            # Cookie extraction from Selenium / SeleniumBase
    ├── notifications.py      # Discord webhook helpers
    └── monitor.py            # Catalog API polling, diff engine, restock detection
```

### Development timeline

| File | Purpose |
|---|---|
| `request.py` | First experiment — raw HTTP request to the ticket portal to understand headers and responses. |
| `main.py` | Version 1 — Selenium-based login + captcha bypass + basic API monitoring loop. |
| `test.py` | Final version — full undetectable Chrome stack, DataDome/reCAPTCHA solvers, deep JSON diffing, automated add-to-cart. |

---

## Requirements

- Python 3.11+
- Google Chrome (latest stable)
- A [CapSolver](https://capsolver.com) account and API key
- A Wimbledon ticket account
- A Discord webhook URL (for notifications)
- (Optional) HTTP proxies for IP rotation

Install Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `LOGIN_EMAIL` | Wimbledon account email |
| `LOGIN_PASSWORD` | Wimbledon account password |
| `API_KEY` | CapSolver API key |
| `DISCORD_WEBHOOK_URL` | Webhook for restock alerts |
| `DISCORD_LOG_WEBHOOK_URL` | Webhook for operational logs (bans, startup) |
| `PROXIES` | Newline-separated proxy list: `IP:PORT` or `IP:PORT:USER:PASS` |

---

## Usage

```bash
python test.py
```

The bot will:
1. Open an undetectable Chrome window
2. Navigate to the Wimbledon ticket portal and solve the entry captcha
3. Log in with your credentials
4. Start polling the catalog API
5. Send a Discord notification when tickets are detected
6. Attempt to add 2 tickets to cart automatically

---

## Disclaimer

This project was built for personal use and educational purposes.  Automated access to ticket platforms may violate their terms of service.  Use responsibly.
