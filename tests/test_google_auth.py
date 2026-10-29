"""
Google sign-in tests — in-process and offline.

Unlike the other suites, this one does not spawn the app on port 5050 against a
copy of the production DB. It imports app.py from *this* checkout into the test
process, with a throwaway DB in a temp dir and Google configured for made-up
accounts. `load_dotenv` is stubbed out so no real .env is read.

Google itself is simulated: discovery metadata and the signing key set are
preloaded into the Authlib client, and the code → token exchange is stubbed to
hand back an ID token signed with a test RSA key. Everything Authlib normally
does on the callback still runs for real — state check, signature, audience,
issuer, expiry and nonce.

Run with: pytest tests/test_google_auth.py -v
"""

import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLIENT_ID = "test-client.apps.googleusercontent.com"
ADMIN_EMAIL = "admin@example.com"
VIEWER_EMAIL = "viewer@example.com"
MAX_ATTEMPTS = 3
# The env-seeded local admin: password + TOTP only works while Google is off.
LOCAL_USER = "breakglass"
LOCAL_PASS = "breakglass-pass-xyz"
LOCAL_TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"

# The ID token the stubbed token endpoint returns next; set per test.
_next_id_token = {}


@pytest.fixture(scope="module")
def pps(tmp_path_factory):
    """The app module, imported against a fresh DB with Google configured."""
    pytest.importorskip("authlib")
    from joserfc.jwk import RSAKey

    if "app" in sys.modules:
        pytest.skip("app already imported in this process")

    mp = pytest.MonkeyPatch()
    work = tmp_path_factory.mktemp("pps_google")
    mp.chdir(work)  # app.py opens vpn_dashboard.db relative to the cwd
    import dotenv
    mp.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    for k, v in {
        "SECRET_KEY":           "test-secret-key-for-google-sign-in-tests",
        "APP_USERNAME":         LOCAL_USER,
        "APP_PASSWORD":         LOCAL_PASS,
        "TOTP_SECRET":          LOCAL_TOTP,
        "COOKIE_INSECURE":      "1",
        "IP_WHITELIST":         "",
        "MAX_LOGIN_ATTEMPTS":   str(MAX_ATTEMPTS),
        "AUTO_CLEANUP_DAYS":    "0",
        # Nothing listens here, so the monitor thread just fails to connect.
        "MT_HOST":              "127.0.0.1",
        "MT_PORT":              "9",
        "GOOGLE_CLIENT_ID":     CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
        # Mixed case on purpose: the allowlist is case-insensitive.
        "GOOGLE_ADMIN_EMAILS":  "Admin@Example.com",
        "GOOGLE_VIEWER_EMAILS": VIEWER_EMAIL,
    }.items():
        mp.setenv(k, v)
    mp.syspath_prepend(REPO)

    import app as app_module

    key = RSAKey.generate_key(2048, parameters={"kid": "test-kid"})
    g = app_module._google
    assert g is not None, "Google sign-in should be active with the env above"
    g.server_metadata.update({
        "issuer": "https://accounts.google.com",
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        "id_token_signing_alg_values_supported": ["RS256"],
        "jwks": {"keys": [key.as_dict(private=False)]},
        "_loaded_at": time.time(),  # so Authlib never fetches discovery
    })

    def fake_fetch_access_token(**params):
        assert params.get("code") == "test-code"
        assert params.get("code_verifier"), "PKCE verifier must be sent"
        return {"access_token": "test-access-token", "token_type": "Bearer",
                "expires_in": 3600, "id_token": _next_id_token.pop("token")}
    mp.setattr(g, "fetch_access_token", fake_fetch_access_token)

    app_module.app.config["TESTING"] = True
    app_module._test_key = key
    yield app_module
    mp.undo()


@pytest.fixture
def client(pps):
    db = sqlite3.connect("vpn_dashboard.db")
    db.execute("DELETE FROM login_attempts")
    db.commit()
    db.close()
    return pps.app.test_client()


def _id_token(pps, nonce, email, verified=True, aud=CLIENT_ID,
              iss="https://accounts.google.com", key=None, exp_in=3600):
    from joserfc import jwt
    now = int(time.time())
    # azp pinned to our client ID so a wrong `aud` is caught by the audience
    # check itself, not by Authlib's azp check.
    claims = {"iss": iss, "aud": aud, "azp": CLIENT_ID, "sub": "1234567890", "email": email,
              "email_verified": verified, "nonce": nonce,
              "iat": now, "exp": now + exp_in}
    return jwt.encode({"alg": "RS256", "kid": "test-kid"}, claims, key or pps._test_key)


def _start(client):
    r = client.get("/auth/google")
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers["Location"]).query)


def _sign_in(pps, client, email, **token_kw):
    q = _start(client)
    _next_id_token["token"] = _id_token(pps, q["nonce"][0], email, **token_kw)
    return client.get(f"/auth/google/callback?state={q['state'][0]}&code=test-code")


def _error_code(r):
    assert r.status_code == 302
    loc = urlparse(r.headers["Location"])
    assert loc.path == "/login"
    return parse_qs(loc.query).get("error", [None])[0]


def _password_login(client):
    """POST valid local-admin credentials + a current TOTP code."""
    html = client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    return client.post("/login", data={
        "csrf_token": token, "username": LOCAL_USER, "password": LOCAL_PASS,
        "code": pyotp.TOTP(LOCAL_TOTP).now()})


def _db(sql, *args):
    db = sqlite3.connect("vpn_dashboard.db")
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


# ── Login page ───────────────────────────────────────────────────────────────

def test_login_page_is_google_only(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'href="/auth/google"' in html
    assert "SIGN IN WITH GOOGLE" in html
    assert 'name="password"' not in html
    assert 'name="code"' not in html
    assert '<form method="post"' not in html


def test_password_login_is_refused_while_google_is_on(client):
    r = _password_login(client)
    assert r.status_code == 200
    assert "Password sign-in is off on this PipSqueeze" in r.get_data(as_text=True)
    with client.session_transaction() as s:
        assert not s.get("logged_in")
    # Refused before the credentials are checked: a stale form isn't a guess.
    assert _db("SELECT * FROM login_attempts") == []
    rows = _db("SELECT username, success, reason FROM login_audit ORDER BY id DESC LIMIT 1")
    assert rows == [(LOCAL_USER, 0, "Password login off (Google only)")]


def test_password_session_is_signed_out_while_google_is_on(client):
    # A session from before Google sign-in was switched on.
    with client.session_transaction() as s:
        s.update(logged_in=True, username=LOCAL_USER, role="admin",
                 last_active=datetime.utcnow().isoformat())
    assert _error_code(client.get("/")) == "password_off"
    with client.session_transaction() as s:
        assert not s.get("logged_in")


def test_login_page_renders_only_known_error_codes(client):
    html = client.get("/login?error=not_allowed").get_data(as_text=True)
    assert "isn&#39;t allowed to sign in to PipSqueeze" in html
    html = client.get("/login?error=<script>alert(1)</script>").get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert 'class="err"' not in html


# ── Redirect to Google ───────────────────────────────────────────────────────

def test_redirect_to_google_carries_state_nonce_and_pkce(client):
    r = client.get("/auth/google")
    assert r.status_code == 302
    loc = urlparse(r.headers["Location"])
    assert loc.netloc == "accounts.google.com"
    q = parse_qs(loc.query)
    assert q["client_id"] == [CLIENT_ID]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://localhost/auth/google/callback"]
    assert q["scope"] == ["openid email"]
    assert q["prompt"] == ["select_account"]
    assert q["code_challenge_method"] == ["S256"]
    assert len(q["state"][0]) >= 20 and len(q["nonce"][0]) >= 20
    assert q["code_challenge"][0]


# ── Successful sign-in ───────────────────────────────────────────────────────

def test_admin_email_signs_in_as_admin(pps, client):
    r = _sign_in(pps, client, ADMIN_EMAIL)
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"
    with client.session_transaction() as s:
        assert s["logged_in"] is True
        assert s["username"] == ADMIN_EMAIL
        assert s["role"] == "admin"
        assert s["auth_source"] == "google"
    page = client.get("/admin/users")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Google sign-in" in html
    assert "http://localhost/auth/google/callback" in html
    assert ADMIN_EMAIL in html and VIEWER_EMAIL in html
    assert "test-client-secret" not in html
    rows = _db("SELECT username, success, reason FROM login_audit ORDER BY id DESC LIMIT 1")
    assert rows == [(ADMIN_EMAIL, 1, "Google")]
    # No admin_users row: Google access lives in .env only.
    assert _db("SELECT 1 FROM admin_users WHERE username=?", ADMIN_EMAIL) == []


def test_viewer_email_is_read_only(pps, client):
    _sign_in(pps, client, VIEWER_EMAIL)
    with client.session_transaction() as s:
        assert s["role"] == "viewer"
    assert client.get("/logs").status_code == 200
    r = client.get("/admin/users")
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"


# ── Refusals ─────────────────────────────────────────────────────────────────

def test_unlisted_account_is_refused_and_counts_toward_lockout(pps, client):
    r = _sign_in(pps, client, "stranger@example.com")
    assert _error_code(r) == "not_allowed"
    with client.session_transaction() as s:
        assert not s.get("logged_in")
    assert _db("SELECT attempts FROM login_attempts WHERE ip='127.0.0.1'") == [(1,)]
    rows = _db("SELECT username, success, reason FROM login_audit ORDER BY id DESC LIMIT 1")
    assert rows == [("stranger@example.com", 0, "Google: not_allowed")]


def test_lockout_blocks_google_sign_in(pps, client):
    for _ in range(MAX_ATTEMPTS):
        _sign_in(pps, client, "stranger@example.com")
    r = client.get("/auth/google")
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/login"
    assert "Too many failed attempts" in client.get("/login").get_data(as_text=True)


def test_unverified_email_is_refused(pps, client):
    assert _error_code(_sign_in(pps, client, ADMIN_EMAIL, verified=False)) == "unverified"
    # Google sends a real boolean; the string "true" is not verified.
    assert _error_code(_sign_in(pps, client, ADMIN_EMAIL, verified="true")) == "unverified"


@pytest.mark.parametrize("token_kw", [
    {"aud": "someone-elses-client.apps.googleusercontent.com"},
    {"iss": "https://evil.example.com"},
    {"exp_in": -3600},
], ids=["wrong-audience", "wrong-issuer", "expired"])
def test_bad_id_token_is_refused(pps, client, token_kw):
    assert _error_code(_sign_in(pps, client, ADMIN_EMAIL, **token_kw)) == "failed"
    with client.session_transaction() as s:
        assert not s.get("logged_in")


def test_forged_signature_is_refused(pps, client):
    from joserfc.jwk import RSAKey
    forger = RSAKey.generate_key(2048, parameters={"kid": "test-kid"})
    assert _error_code(_sign_in(pps, client, ADMIN_EMAIL, key=forger)) == "failed"


def test_replayed_nonce_is_refused(pps, client):
    q = _start(client)
    _next_id_token["token"] = _id_token(pps, "not-the-nonce-we-sent", ADMIN_EMAIL)
    r = client.get(f"/auth/google/callback?state={q['state'][0]}&code=test-code")
    assert _error_code(r) == "failed"


def test_callback_without_matching_state_is_refused(client):
    _start(client)
    r = client.get("/auth/google/callback?state=forged-state&code=test-code")
    assert _error_code(r) == "expired"
    # A stale round trip is not a password guess.
    assert _db("SELECT * FROM login_attempts") == []


def test_cancel_at_google(client):
    r = client.get("/auth/google/callback?error=access_denied&state=x")
    assert _error_code(r) == "cancelled"
    assert _db("SELECT * FROM login_attempts") == []


def test_not_configured_falls_back_to_password_login(pps, client, monkeypatch):
    monkeypatch.setattr(pps, "_google", None)
    assert _error_code(client.get("/auth/google")) == "not_configured"
    html = client.get("/login").get_data(as_text=True)
    assert "SIGN IN WITH GOOGLE" not in html
    assert 'name="password"' in html and 'name="code"' in html
    # With Google off, password + TOTP is how you get back in.
    r = _password_login(client)
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"
    assert client.get("/").status_code == 200


# ── Allowlist re-checked on every request ────────────────────────────────────

def test_removing_email_signs_user_out(pps, client, monkeypatch):
    _sign_in(pps, client, ADMIN_EMAIL)
    assert client.get("/").status_code == 200
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "someone-else@example.com")
    assert _error_code(client.get("/")) == "not_allowed"
    with client.session_transaction() as s:
        assert not s.get("logged_in")


def test_demotion_lands_on_next_request(pps, client, monkeypatch):
    _sign_in(pps, client, ADMIN_EMAIL)
    assert client.get("/admin/users").status_code == 200
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "")
    monkeypatch.setenv("GOOGLE_VIEWER_EMAILS", ADMIN_EMAIL)
    r = client.get("/admin/users")
    assert r.status_code == 302 and urlparse(r.headers["Location"]).path == "/"
    with client.session_transaction() as s:
        assert s["role"] == "viewer"


# ── google_auth config helpers ───────────────────────────────────────────────

def test_config_problems(pps, monkeypatch):
    import google_auth
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
              "GOOGLE_ADMIN_EMAILS", "GOOGLE_VIEWER_EMAILS"):
        monkeypatch.setenv(k, "")
    assert google_auth.config_problems() == []      # all unset = off, not broken
    assert not google_auth.is_configured()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    problems = google_auth.config_problems()
    assert any("GOOGLE_CLIENT_SECRET" in p for p in problems)
    assert any("nobody could sign in" in p for p in problems)
    # A .env inline comment after an empty value must not count as a value.
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "# comma-separated emails")
    assert google_auth.role_for("# comma-separated emails") is None


def test_role_for_prefers_admin(pps, monkeypatch):
    import google_auth
    monkeypatch.setenv("GOOGLE_ADMIN_EMAILS", "a@x.com, Both@X.com")
    monkeypatch.setenv("GOOGLE_VIEWER_EMAILS", "both@x.com,v@x.com")
    assert google_auth.role_for("BOTH@x.com") == "admin"
    assert google_auth.role_for("v@x.com") == "viewer"
    assert google_auth.role_for("nobody@x.com") is None
    assert google_auth.role_for(None) is None


# ── Startup: password creds only required while Google is off ────────────────
# app.py checks these at import time, so each case runs in a fresh process
# (temp dir, throwaway DB, no .env read).

_BOOT = r"""
import sqlite3, sys
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
sys.path.insert(0, sys.argv[1])
try:
    import app
except RuntimeError as e:
    print("REFUSED:", e)
else:
    n = sqlite3.connect("vpn_dashboard.db").execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    print("STARTED admin_users=%d google=%s" % (n, app._google is not None))
"""

_GOOGLE_ENV = {
    "GOOGLE_CLIENT_ID":     CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_ADMIN_EMAILS":  ADMIN_EMAIL,
}


def _boot(tmp_path, extra):
    import subprocess
    drop = {"APP_USERNAME", "APP_PASSWORD", "TOTP_SECRET", "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET", "GOOGLE_ADMIN_EMAILS", "GOOGLE_VIEWER_EMAILS"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update({"SECRET_KEY": "boot-test-secret", "COOKIE_INSECURE": "1",
                "MT_HOST": "127.0.0.1", "MT_PORT": "9", **extra})
    r = subprocess.run([sys.executable, "-I", "-c", _BOOT, REPO], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=60)
    lines = [l for l in r.stdout.splitlines() if l.startswith(("STARTED", "REFUSED"))]
    assert lines, r.stdout + r.stderr
    return lines[-1]


def test_google_only_boots_without_password_creds_and_seeds_no_admin(tmp_path):
    # No APP_USERNAME / APP_PASSWORD / TOTP_SECRET at all.
    assert _boot(tmp_path, _GOOGLE_ENV) == "STARTED admin_users=0 google=True"


def test_refuses_to_boot_without_password_creds_while_google_is_off(tmp_path):
    out = _boot(tmp_path, {})
    assert out.startswith("REFUSED: APP_USERNAME and APP_PASSWORD must be set")
    out = _boot(tmp_path, {"APP_USERNAME": "admin", "APP_PASSWORD": "pw-xyz"})
    assert out.startswith("REFUSED: TOTP_SECRET must be set")


def test_half_configured_google_still_needs_password_creds(tmp_path):
    # Secret missing → Google is off → the password creds are the login.
    half = {k: v for k, v in _GOOGLE_ENV.items() if k != "GOOGLE_CLIENT_SECRET"}
    assert _boot(tmp_path, half).startswith("REFUSED: APP_USERNAME and APP_PASSWORD")


def test_seeds_admin_from_env_when_creds_are_set(tmp_path):
    out = _boot(tmp_path, {"APP_USERNAME": "admin", "APP_PASSWORD": "pw-xyz",
                           "TOTP_SECRET": LOCAL_TOTP})
    assert out == "STARTED admin_users=1 google=False"
