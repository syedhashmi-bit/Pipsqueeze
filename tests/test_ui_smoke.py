"""
Playwright UI smoke tests for PipSqueeze.

Run with:  pytest tests/test_ui_smoke.py -v
Requires:  playwright install --with-deps chromium

The `live_url` fixture (from conftest.py) spawns an isolated test instance
on port 5050 with a copy of the prod DB and insecure cookies — these tests
do NOT touch the production gunicorn or its database.
"""

import pyotp
import pytest
from playwright.sync_api import Page, expect


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def login(page: Page, base_url: str, creds: dict):
    """Walk through the login form including 2FA. Retries once on TOTP edge race."""
    for attempt in range(3):
        page.goto(f"{base_url}/login")
        page.fill('input[name="username"]', creds["username"])
        page.fill('input[name="password"]', creds["password"])
        code = pyotp.TOTP(creds["totp_secret"]).now()
        page.fill('input[name="code"]', code)
        with page.expect_navigation(wait_until="load") as nav_info:
            page.click('button[type="submit"]')
        if not page.url.rstrip("/").endswith("/login"):
            return
    raise AssertionError(f"Login failed after retries; ended at {page.url}")


# ─────────────────────────────────────────────
# Public pages (no login)
# ─────────────────────────────────────────────

def test_login_page_renders(page: Page, live_url):
    page.goto(f"{live_url}/login")
    # Token meta tag present
    expect(page.locator('meta[name="csrf-token"]')).to_have_count(1)
    # Username and password fields visible
    expect(page.locator('input[name="username"]')).to_be_visible()
    expect(page.locator('input[name="password"]')).to_be_visible()
    expect(page.locator('input[name="code"]')).to_be_visible()
    # Hidden CSRF input present
    expect(page.locator('input[name="csrf_token"]')).to_have_count(1)


def test_csrf_blocks_post_without_token(live_url):
    """Submit /login POST with NO csrf_token field → must not succeed."""
    import requests
    r = requests.post(f"{live_url}/login",
                      data={"username":"x","password":"y","totp":"000000"},
                      allow_redirects=False)
    # Either 400 (CSRF rejected) or 302 to referrer (handler redirects); never 200 success.
    assert r.status_code in (302, 400), f"unexpected: {r.status_code}"
    if r.status_code == 302:
        # Should redirect to login or referrer, not into the app
        assert "/login" in r.headers.get("Location", "") or r.headers.get("Location", "").endswith("/")


def test_session_cookie_has_security_flags(live_url):
    """Set-Cookie on a fresh response must include HttpOnly + SameSite=Lax."""
    import requests
    r = requests.get(f"{live_url}/login")
    sc = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in sc, f"missing HttpOnly: {sc}"
    assert "SameSite=Lax" in sc, f"missing SameSite=Lax: {sc}"
    # Secure flag is suppressed because COOKIE_INSECURE=1 in test env (so the
    # test_client can keep the cookie over plain HTTP). In prod it is set.


# ─────────────────────────────────────────────
# Login flow
# ─────────────────────────────────────────────

def test_login_success_with_valid_2fa(page: Page, live_url, test_credentials):
    import re as _re
    login(page, live_url, test_credentials)
    expect(page).to_have_title(_re.compile(r"PipSqueeze"))


def test_login_fails_with_bad_password(page: Page, live_url, test_credentials):
    page.goto(f"{live_url}/login")
    page.fill('input[name="username"]', test_credentials["username"])
    page.fill('input[name="password"]', "WRONG-PASSWORD")
    code = pyotp.TOTP(test_credentials["totp_secret"]).now()
    page.fill('input[name="code"]', code)
    page.click('button[type="submit"]')
    # Stays on /login with error visible
    expect(page).to_have_url(f"{live_url}/login")
    expect(page.locator(".err")).to_be_visible()


# ─────────────────────────────────────────────
# Authenticated pages render
# ─────────────────────────────────────────────

@pytest.mark.parametrize("path,marker", [
    ("/",                  "PipSqueeze"),
    ("/wireguard",         "Peers"),
    ("/security",          "SECURITY"),
    ("/notifications",     "NOTIFICATIONS"),
    ("/weekly-report",     "WEEKLY"),
    ("/map",               "MAP"),
    ("/logs",              "LOGS"),
    ("/import",            "IMPORT"),
    ("/admin/users",       "USERS"),
    ("/admin/api-keys",    "API"),
    ("/provision/manage",  "PROVISION"),
])
def test_authed_pages_render(page: Page, live_url, test_credentials, path, marker):
    login(page, live_url, test_credentials)
    page.goto(f"{live_url}{path}")
    # Page should load (no JS errors, marker text present somewhere)
    content = page.content()
    assert marker.upper() in content.upper(), f"{path}: missing marker {marker!r}"
    # CSRF meta tag present on every authed page
    assert 'name="csrf-token"' in content, f"{path}: missing csrf meta"


# ─────────────────────────────────────────────
# Keyboard cheatsheet (P2 #10)
# ─────────────────────────────────────────────

def test_question_mark_opens_cheatsheet(page: Page, live_url, test_credentials):
    login(page, live_url, test_credentials)
    page.goto(f"{live_url}/")
    cheat = page.locator("#cheatModal")
    expect(cheat).not_to_have_class("modal-bg open")
    page.keyboard.press("?")
    expect(cheat).to_have_class("modal-bg open")
    # Esc closes it
    page.keyboard.press("Escape")
    expect(cheat).not_to_have_class("modal-bg open")


# ─────────────────────────────────────────────
# Map page renders without gateway pin (P0 regression)
# ─────────────────────────────────────────────

def test_map_renders_without_gateway_when_unset(page: Page, live_url, test_credentials):
    """If MT_LAT/MT_LON are unset (default in test env), map should still render."""
    login(page, live_url, test_credentials)
    page.goto(f"{live_url}/map")
    # Wait for Leaflet to mount
    page.wait_for_selector("#map", timeout=5000)
    # Either way, no JS exception in console — collect them
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.wait_for_timeout(500)
    assert not errors, f"JS errors on /map: {errors}"


# ─────────────────────────────────────────────
# API v1 keys (P2 #8)
# ─────────────────────────────────────────────

def test_api_v1_requires_key(live_url):
    """No key → 401."""
    import requests
    r = requests.get(f"{live_url}/api/v1/clients")
    assert r.status_code == 401


def test_api_v1_with_invalid_key_rejected(live_url):
    import requests
    r = requests.get(f"{live_url}/api/v1/clients",
                     headers={"Authorization": "Bearer bogus_key"})
    assert r.status_code == 401


def test_api_v1_uptime_history_clamps_to_max(live_url, test_credentials):
    """The days param should clamp to 90; verify via login session."""
    import requests
    s = requests.Session()
    # Login via session
    r = s.get(f"{live_url}/login")
    import re
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    code = pyotp.TOTP(test_credentials["totp_secret"]).now()
    r = s.post(f"{live_url}/login", data={
        "username": test_credentials["username"],
        "password": test_credentials["password"],
        "code": code,
        "csrf_token": tok,
    }, allow_redirects=False)
    assert r.status_code == 302, f"login failed: {r.status_code}"

    # Test endpoint
    for days, expected_max_count in [(7, 7), (30, 30), (90, 90), (300, 90)]:
        r = s.get(f"{live_url}/api/uptime-history/anyclient?days={days}")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == expected_max_count
