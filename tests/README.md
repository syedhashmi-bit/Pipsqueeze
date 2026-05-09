# PipSqueeze Test Suite

Two layers of tests:

## 1. HTTP-only smoke tests (`test_http_smoke.py`)

Browser-less tests using Flask's `test_client`. Cover every P0/P1/P2 change:
CSRF protection, session-cookie flags, Fernet encryption at rest, ipapi.co
geolocation, service-worker versioning, import flow, API keys, auto-cleanup
helper, the keyboard cheatsheet modal, and uptime-history range clamping.

```bash
source venv/bin/activate
pip install pytest pyotp
pytest tests/test_http_smoke.py -v
```

These run in seconds and require no system packages beyond Python.

## 2. Playwright UI smoke tests (`test_ui_smoke.py`)

Real browser tests that drive the full UI. Checks login flow with TOTP, page
rendering, the `?` cheatsheet shortcut, the map regression for missing gateway
coords, and CSRF/session-cookie behavior end-to-end through Chromium.

One-time setup (requires apt — needs root or sudo):

```bash
sudo apt install -y libnspr4 libnss3 libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libasound2t64 libgbm1 libxext6 libcairo2 libpango-1.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libatspi2.0-0t64
source venv/bin/activate
playwright install chromium
```

Then run:

```bash
pytest tests/test_ui_smoke.py -v
```

The `live_url` fixture (in `conftest.py`) spawns an isolated test gunicorn on
port 5050 with a copy of `vpn_dashboard.db`, deterministic admin credentials,
and `COOKIE_INSECURE=1` so cookies survive over plain HTTP. Tests never touch
the production gunicorn or its DB.

## Running everything

```bash
pytest tests/ -v
```
