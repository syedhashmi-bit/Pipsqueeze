"""
Pytest fixtures for PipSqueeze test suite.

Boots an isolated Flask test instance on port 5050 with:
- An ephemeral SQLite DB copied from the live one (so tests have realistic schema
  & sample data but never write to the real one)
- COOKIE_INSECURE=1 so cookies are sent over plain HTTP from the test client
- A deterministic TOTP secret so 2FA flows are reproducible
- AUTO_CLEANUP_DAYS=0 so the monitor doesn't surprise-delete

Tests can be run with: pytest tests/ -v
Use the `live_url` fixture to talk to the running test instance.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import socket
import pytest
import urllib.request
from werkzeug.security import generate_password_hash

PORT = 5050
TEST_USER = "pwtest_admin"
TEST_PASS = "pwtest_pass_secure_xyz"
# Fixed Base32 TOTP secret for deterministic 2FA codes during tests
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


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
def test_db_path(tmp_path_factory):
    """Copy the production DB to a tmp file so tests don't touch the real one."""
    src = "/var/www/pipsqueeze/vpn_dashboard.db"
    dst = tmp_path_factory.mktemp("pps") / "vpn_dashboard.db"
    if os.path.exists(src):
        shutil.copy(src, dst)
    # Seed the test admin user (overwriting any existing) so login works deterministically
    conn = sqlite3.connect(dst)
    conn.execute(
        "INSERT OR REPLACE INTO admin_users (username, password_hash, role, created_at) "
        "VALUES (?,?,?,?)",
        (TEST_USER, generate_password_hash(TEST_PASS), "admin", "2026-01-01T00:00:00"),
    )
    # Wipe login_attempts so the test IP isn't pre-locked
    conn.execute("DELETE FROM login_attempts")
    conn.commit()
    conn.close()
    return str(dst)


@pytest.fixture(scope="session")
def live_url(test_db_path):
    """Spawn a separate gunicorn on PORT, against the test DB, with insecure cookies."""
    env = {**os.environ}
    # Override secrets just for the test instance
    env.update({
        "COOKIE_INSECURE":   "1",
        "APP_USERNAME":      TEST_USER,
        "APP_PASSWORD":      TEST_PASS,
        "TOTP_SECRET":       TEST_TOTP_SECRET,
        "SECRET_KEY":        "test-secret-key-for-pipsqueeze-pytest-suite-do-not-use-in-prod",
        "AUTO_CLEANUP_DAYS": "0",
        "IP_WHITELIST":      "",   # disable whitelist for tests
    })

    # Run the test gunicorn from a temp dir that contains a symlink tree to the project,
    # but with vpn_dashboard.db replaced. Simplest: change CWD via a wrapper.
    proj = "/var/www/pipsqueeze"
    # Use a small launcher that swaps the DB path before importing app
    launcher = tempfile.NamedTemporaryFile(mode="w", suffix="_pps_launch.py", delete=False)
    launcher.write(f"""
import os, sys
sys.path.insert(0, {proj!r})
os.chdir({proj!r})
# Hack: redirect DB_FILE before app imports
import sqlite3 as _sq
_orig_connect = _sq.connect
def _redirect(path, *a, **kw):
    if path == 'vpn_dashboard.db':
        path = {test_db_path!r}
    return _orig_connect(path, *a, **kw)
_sq.connect = _redirect

# Also patch notifications module's DB_FILE before it imports
import notifications
notifications.DB_FILE = {test_db_path!r}
import vault
vault.DB_FILE = {test_db_path!r}

from app import app
if __name__ == '__main__':
    app.run(host='127.0.0.1', port={PORT}, debug=False, use_reloader=False)
""")
    launcher.close()

    proc = subprocess.Popen(
        ["python", launcher.name],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=proj,
    )
    if not _wait_port("127.0.0.1", PORT):
        proc.kill()
        out = proc.stdout.read().decode(errors="replace")
        raise RuntimeError(f"Test app failed to start:\n{out[-3000:]}")

    yield f"http://127.0.0.1:{PORT}"

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    os.unlink(launcher.name)


@pytest.fixture(scope="session")
def totp_secret():
    return TEST_TOTP_SECRET


@pytest.fixture(scope="session")
def test_credentials():
    return {"username": TEST_USER, "password": TEST_PASS, "totp_secret": TEST_TOTP_SECRET}
