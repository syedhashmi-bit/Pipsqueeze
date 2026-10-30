"""
Page-structure tests for the shared base template (templates/base.html).

Each test runs a short script in a fresh process: it imports app.py from this
checkout in a temp dir (throwaway DB, load_dotenv stubbed so no .env is read,
MikroTik pointed at a closed port, Google sign-in off), drives Flask's test
client, and prints a JSON result. A fresh process per test keeps these
independent of test_google_auth.py, which imports the app in-process with
Google configured.

Run with: pytest tests/test_pages.py -v
"""

import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HARNESS = r"""
import json, re, sys, sqlite3
from datetime import datetime
import dotenv
dotenv.load_dotenv = lambda *a, **k: False
sys.path.insert(0, sys.argv[1])
import app as A
{setup}
c = A.app.test_client()
def login(role="admin"):
    with c.session_transaction() as s:
        s.update(logged_in=True, username="admin", role=role,
                 last_active=datetime.utcnow().isoformat())
def csrf(path):
    html = c.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
out = {{}}
{body}
print("RESULT " + json.dumps(out))
"""

ENV = {
    "SECRET_KEY": "pages-test-secret", "APP_USERNAME": "admin",
    "APP_PASSWORD": "pages-test-pass", "TOTP_SECRET": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    "COOKIE_INSECURE": "1", "IP_WHITELIST": "", "AUTO_CLEANUP_DAYS": "0",
    "MT_HOST": "127.0.0.1", "MT_PORT": "9",
}
DROP = {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ADMIN_EMAILS", "GOOGLE_VIEWER_EMAILS"}


def _run(tmp_path, body, setup="", extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    env.update(ENV)
    env.update(extra_env or {})
    script = HARNESS.format(setup=setup, body=body)
    r = subprocess.run([sys.executable, "-I", "-c", script, REPO], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=90)
    line = [l for l in r.stdout.splitlines() if l.startswith("RESULT ")]
    assert line, r.stdout[-2000:] + r.stderr[-3000:]
    return json.loads(line[-1][len("RESULT "):])


def test_every_page_has_one_nav_with_its_section_active(tmp_path):
    out = _run(tmp_path, r"""
login()
for path in ["/", "/wireguard", "/map", "/weekly-report", "/security", "/settings",
             "/notifications", "/admin/users", "/admin/api-keys", "/provision/manage", "/logs"]:
    html = c.get(path).get_data(as_text=True)
    out[path] = {
        "appbars": html.count('class="appbar"'),
        "active": re.findall(r'class="navlink active"[^>]*>([^<]+)<', html),
        "toasts": html.count('id="toast"'),
        "old_bg": "scanlines" in html or 'class="bg-layer"' in html,
    }
""")
    expected = {"/": "Clients", "/wireguard": "Peers", "/map": "Map",
                "/weekly-report": "Reports", "/security": "Security"}
    for path, got in out.items():
        assert got["appbars"] == 1, path
        assert got["active"] == [expected.get(path, "Settings")], (path, got["active"])
        assert got["toasts"] == 1, path
        assert not got["old_bg"], path


def test_peers_page_survives_a_router_outage(tmp_path):
    out = _run(tmp_path, r"""
login()
r = c.get("/wireguard")
out["page"] = [r.status_code, "reach the MikroTik router" in r.get_data(as_text=True)]
r = c.get("/peers")
out["json"] = [r.status_code, r.get_json()]
""")
    assert out["page"] == [200, True]
    status, body = out["json"]
    assert status == 503 and body["error"] == "MikroTik unreachable"


def test_error_pages_use_the_app_layout(tmp_path):
    out = _run(tmp_path, setup=r"""
@A.app.route("/boom")
def _boom():
    raise RuntimeError("deliberate test failure")
""", body=r"""
login()
r = c.get("/no-such-page"); h = r.get_data(as_text=True)
out["404"] = [r.status_code, "Page not found" in h, 'class="appbar"' in h]
r = c.get("/api/no-such-thing")
out["api404"] = [r.status_code, r.get_json()]
r = c.get("/boom"); h = r.get_data(as_text=True)
out["500"] = [r.status_code, "Something went wrong" in h, "deliberate test failure" in h]
""")
    assert out["404"] == [404, True, True]
    assert out["api404"] == [404, {"error": "not found"}]
    # Styled page, and the exception text is not leaked to the browser.
    assert out["500"] == [500, True, False]


def test_reset_routes_require_typing_reset(tmp_path):
    out = _run(tmp_path, r"""
db = sqlite3.connect("vpn_dashboard.db")
db.execute("INSERT INTO clients (name, ip, created_at) VALUES ('keepme', '10.10.0.9', '2026-01-01')")
db.commit(); db.close()
count = lambda: sqlite3.connect("vpn_dashboard.db").execute("SELECT COUNT(*) FROM clients").fetchone()[0]
login()
tok = csrf("/settings")
r = c.post("/reset-db", data={"csrf_token": tok, "confirm": "reset"})
out["wrong"] = [r.status_code, r.headers.get("Location", ""), count()]
r = c.post("/reset-db", data={"csrf_token": tok, "confirm": "RESET"})
out["right"] = [r.status_code, r.headers.get("Location", ""), count()]
""")
    status, loc, n = out["wrong"]
    assert status == 302 and loc.endswith("/settings") and n == 1
    status, loc, n = out["right"]
    assert status == 302 and loc.endswith("/") and n == 0


def test_flash_messages_show_on_the_dashboard(tmp_path):
    out = _run(tmp_path, r"""
login()
with c.session_transaction() as s:
    s["_flashes"] = [("message", "Client demo created successfully"),
                     ("error", "MikroTik error: timed out")]
h = c.get("/").get_data(as_text=True)
out["ok"] = bool(re.search(r'class="flash"[^>]*>\s*<span class="flash-text">Client demo created successfully', h))
out["err"] = bool(re.search(r'class="flash error"[^>]*>\s*<span class="flash-text">MikroTik error', h))
""")
    assert out == {"ok": True, "err": True}


def test_dashboard_controls_by_role(tmp_path):
    out = _run(tmp_path, r"""
db = sqlite3.connect("vpn_dashboard.db")
db.execute("INSERT INTO clients (name, ip, portal_token, created_at) VALUES ('demo', '10.10.0.2', 'tok123', '2026-01-01')")
db.commit(); db.close()
for role in ("admin", "viewer"):
    login(role)
    h = c.get("/").get_data(as_text=True)
    s = c.get("/settings").get_data(as_text=True)
    out[role] = {
        "new_client": 'id="createModal"' in h,
        "row_menu": 'data-menu="menu-' in h,
        "create_fields": sorted(set(re.findall(r'name="([a-z_]+)"', h.split('id="createModal"')[1].split('id="renameModal"')[0])))
                         if 'id="createModal"' in h else [],
        "danger_zone": "Reset database" in s,
    }
""")
    admin, viewer = out["admin"], out["viewer"]
    assert admin["new_client"] and admin["row_menu"] and admin["danger_zone"]
    # The create form still posts every field home() reads.
    for field in ["client", "tags", "notes", "expires_at", "quota_amount", "quota_unit",
                  "location", "lat", "lon", "access_mode", "csrf_token"]:
        assert field in admin["create_fields"], field
    assert not (viewer["new_client"] or viewer["row_menu"] or viewer["danger_zone"])


def test_csp_allows_every_external_host_the_templates_load(tmp_path):
    # Leaflet and Chart.js were loaded from cdnjs while the CSP didn't list it,
    # so /map and the peer charts were silently blank.
    out = _run(tmp_path, r"""
out["csp"] = c.get("/login").headers["Content-Security-Policy"]
""")
    directives = dict(d.strip().split(" ", 1) for d in out["csp"].split(";") if d.strip())
    tdir = os.path.join(REPO, "templates")
    for name in os.listdir(tdir):
        html = open(os.path.join(tdir, name)).read()
        for host in re.findall(r'<script[^>]+src="(https://[^/"]+)', html):
            assert host in directives["script-src"], (name, host)
        for host in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="(https://[^/"]+)', html):
            assert host in directives["style-src"], (name, host)


def test_service_worker_leaves_other_origins_alone():
    # Inside the worker, fetch() runs under the worker's CSP (connect-src), which
    # doesn't list Google Fonts or the CDNs, so proxying them broke fonts, Leaflet
    # and Chart.js on every page after the first visit.
    sw = open(os.path.join(REPO, "static", "sw.js")).read()
    handler = sw.split("addEventListener('fetch'", 1)[1]
    guard = handler.index("url.origin !== self.location.origin")
    assert guard < handler.index("respondWith"), "origin check must come before any respondWith"


# ── Public pages (templates/public_base.html) ────────────────────────────────

def test_public_pages_render_without_the_app_chrome(tmp_path):
    out = _run(tmp_path, setup=r"""
import os
A._wg_keypair = lambda: ("cHJpdmF0ZS1rZXk=", "cHVibGljLWtleQ==")  # no `wg` binary needed
db = sqlite3.connect("vpn_dashboard.db")
db.execute("INSERT INTO clients (name, ip, portal_token, created_at) VALUES ('demo', '10.10.0.2', 'tok-demo', '2026-01-01')")
db.execute("INSERT INTO clients (name, ip, portal_token, created_at) VALUES ('noconf', '10.10.0.3', 'tok-noconf', '2026-01-01')")
db.execute("INSERT INTO provision_tokens (token, label, created_at) VALUES ('ptok', 'Alex laptop', '2026-01-01')")
db.commit(); db.close()
os.makedirs("clients", exist_ok=True)
open("clients/demo.conf", "w").write("[Interface]\n")
""", body=r"""
def page(path):
    r = c.get(path); h = r.get_data(as_text=True)
    return {
        "status": r.status_code,
        "app_css": 'href="/static/css/app.css"' in h,
        "public": 'class="public-card"' in h,
        "chrome": 'class="appbar"' in h or 'id="mainNav"' in h or "Log out" in h,
        "old_bg": "scanlines" in h or 'class="bg-layer"' in h,
        "download": 'href="/portal/' in h and '/download"' in h,
        "qr": 'src="/portal/' in h and '/qr"' in h,
        "no_conf": "Config file not available" in h,
        "used": "This link has been used" in h,
        "ip_hint": "IP_WHITELIST=127.0.0.1" in h,
    }
out["login"] = page("/login")
out["portal"] = page("/portal/tok-demo")
out["portal_noconf"] = page("/portal/tok-noconf")
out["provision"] = page("/provision/ptok")
out["provision_again"] = page("/provision/ptok")
out["provision_bad"] = page("/provision/no-such-token")
A.IP_WHITELIST_RAW = "203.0.113.5"
out["blocked"] = page("/login")
""")
    expected_status = {"login": 200, "portal": 200, "portal_noconf": 200, "provision": 200,
                       "provision_again": 410, "provision_bad": 410, "blocked": 403}
    for name, got in out.items():
        assert got["status"] == expected_status[name], (name, got["status"])
        assert got["app_css"] and got["public"], name
        assert not got["chrome"], name
        assert not got["old_bg"], name
    assert out["portal"]["download"] and out["portal"]["qr"] and not out["portal"]["no_conf"]
    assert out["portal_noconf"]["no_conf"] and not out["portal_noconf"]["download"]
    assert out["provision"]["download"] and out["provision"]["qr"] and out["provision"]["used"]
    assert out["blocked"]["ip_hint"]


def test_login_form_errors_flashes_and_lockout(tmp_path):
    out = _run(tmp_path, r"""
from datetime import timedelta
h = c.get("/login").get_data(as_text=True)
form = h.split('<form method="post"', 1)[1].split("</form>", 1)[0]
out["fields"] = sorted(re.findall(r'name="([a-z_]+)"', form))
out["submits"] = form.count('type="submit"')
out["csrf_meta"] = 'name="csrf-token"' in h
out["no_error"] = 'class="err"' not in h

r = c.post("/login", data={"csrf_token": csrf("/login"), "username": "admin",
                           "password": "wrong", "code": "000000"})
h = r.get_data(as_text=True)
out["bad_password"] = [r.status_code, bool(re.search(r'class="err"[^>]*>.*?Invalid username or password', h, re.S))]

# Session expiry and CSRF failures flash, then redirect here.
with c.session_transaction() as s:
    s["_flashes"] = [("message", "Session expired. Please log in again.")]
h = c.get("/login").get_data(as_text=True)
out["flash"] = bool(re.search(r'class="flash"[^>]*>\s*<span class="flash-text">Session expired', h))

db = sqlite3.connect("vpn_dashboard.db")
db.execute("DELETE FROM login_attempts")
db.execute("INSERT INTO login_attempts (ip, attempts, locked_until, last_attempt) VALUES ('127.0.0.1', 5, ?, ?)",
           ((datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
db.commit(); db.close()
h = c.get("/login").get_data(as_text=True)
out["locked"] = {
    "notice": bool(re.search(r'class="err"[^>]*>.*?Too many failed attempts.*?<strong[^>]*>10 minute', h, re.S)),
    "form_off": '<form method="post" class="is-locked"' in h,
    "countdown": "document.querySelector('.err strong')" in h,
}
""")
    assert out["fields"] == ["code", "csrf_token", "password", "username"]
    assert out["submits"] == 1 and out["csrf_meta"] and out["no_error"]
    assert out["bad_password"] == [200, True]
    assert out["flash"]
    assert out["locked"] == {"notice": True, "form_off": True, "countdown": True}


def test_public_templates_use_tokens_not_hex_colours():
    # Hex colours break the light theme; public pages use app.css tokens.
    tdir = os.path.join(REPO, "templates")
    for name in ["login.html", "portal.html", "provision.html", "provision_error.html", "blocked.html"]:
        html = open(os.path.join(tdir, name)).read()
        assert '{% extends "public_base.html" %}' in html, name
        assert "<style" not in html, name
        assert not re.findall(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b", html), name
