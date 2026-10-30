"""
Pytest fixtures for PipSqueeze test suite.

`live_url` boots an isolated instance of the app from THIS checkout, in a temp
directory, on a free port:
- A throwaway SQLite DB created by the app's own init_db() — never a copy of the
  production DB (CLAUDE.md: never read vpn_dashboard.db)
- No .env is read (load_dotenv is stubbed), so no live secrets or settings leak
  in; MikroTik points at a closed local port, so the monitor thread can never
  reach (or disable peers on) the real router; Google sign-in is off
- COOKIE_INSECURE=1 so cookies are sent over plain HTTP from the test client
- A deterministic TOTP secret so 2FA flows are reproducible
- AUTO_CLEANUP_DAYS=0 so the monitor doesn't surprise-delete

Tests can be run with: pytest tests/ -v
UI tests need a browser: `playwright install chromium`, or use the system
Chrome with `pytest tests/test_ui_smoke.py --browser-channel chrome`.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_USER = "pwtest_admin"
TEST_PASS = "pwtest_pass_secure_xyz"
# Fixed Base32 TOTP secret for deterministic 2FA codes during tests
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"

# Environment for an isolated app instance (shared with test_http_smoke.py).
ISOLATED_ENV = {
    "COOKIE_INSECURE":   "1",
    "APP_USERNAME":      TEST_USER,
    "APP_PASSWORD":      TEST_PASS,
    "TOTP_SECRET":       TEST_TOTP_SECRET,
    "SECRET_KEY":        "test-secret-key-for-pipsqueeze-pytest-suite-do-not-use-in-prod",
    "AUTO_CLEANUP_DAYS": "0",
    "IP_WHITELIST":      "",
    "MT_HOST":           "127.0.0.1",
    "MT_PORT":           "9",
}
GOOGLE_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ADMIN_EMAILS", "GOOGLE_VIEWER_EMAILS")

LAUNCHER = """
import sys
import dotenv
dotenv.load_dotenv = lambda *a, **k: False   # never read a real .env
sys.path.insert(0, {repo!r})
from app import app
app.run(host="127.0.0.1", port={port}, debug=False, use_reloader=False)
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host, port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


@pytest.fixture(scope="session")
def test_workdir(tmp_path_factory):
    return tmp_path_factory.mktemp("pps")


@pytest.fixture(scope="session")
def test_db_path(test_workdir):
    """The live_url instance's DB (created by its init_db on startup)."""
    return str(test_workdir / "vpn_dashboard.db")


@pytest.fixture(scope="session")
def live_url(test_workdir, test_db_path):
    """Spawn the app from this checkout in test_workdir, with insecure cookies."""
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in GOOGLE_ENV}
    env.update(ISOLATED_ENV)
    launcher = test_workdir / "launch.py"
    launcher.write_text(LAUNCHER.format(repo=REPO, port=port))
    log_path = test_workdir / "app.log"

    # Output goes to a file: an unread pipe fills up and stalls the app.
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-I", str(launcher)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=test_workdir,
        )
    if not _wait_port("127.0.0.1", port):
        proc.kill()
        out = log_path.read_text(errors="replace")
        raise RuntimeError(f"Test app failed to start:\n{out[-3000:]}")

    yield f"http://127.0.0.1:{port}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def totp_secret():
    return TEST_TOTP_SECRET


@pytest.fixture(scope="session")
def test_credentials():
    return {"username": TEST_USER, "password": TEST_PASS, "totp_secret": TEST_TOTP_SECRET}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Browser tests run with reduced motion, which turns off the cross-page
    view transition (transitions.css). Headless Chrome stops painting frames
    during one after a same-tab navigation, so Playwright never sees an
    element as stable and every click there times out."""
    return {**browser_context_args, "reduced_motion": "reduce"}
