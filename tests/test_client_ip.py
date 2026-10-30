"""
Client-IP tests: the whitelist, login lockout and audit log all key on
get_client_ip(), so a visitor must not be able to choose it.

nginx forwards X-Forwarded-For as "<whatever the browser sent>, <real IP>",
and a LAN install has no nginx at all, so the browser's header reaches the
app untouched. Uses the fresh-process harness from test_pages.py.

Run with: pytest tests/test_client_ip.py -v
"""

from tests.test_pages import _run

ROUTE = r"""
@A.app.route("/_whoami")
def _whoami():
    from flask import request
    return {"ip": A.get_client_ip(), "scheme": request.scheme}
"""


def _ask(remote, xff=None, proto=None):
    headers = {}
    if xff:
        headers["X-Forwarded-For"] = xff
    if proto:
        headers["X-Forwarded-Proto"] = proto
    return (f"c.get('/_whoami', headers={headers!r}, "
            f"environ_base={{'REMOTE_ADDR': {remote!r}}}).get_json()")


def test_forged_header_behind_nginx_does_not_win(tmp_path):
    out = _run(tmp_path, setup=ROUTE, body=f"""
out["nginx"] = {_ask("127.0.0.1", "1.2.3.4, 203.0.113.9", "https")}
out["plain"] = {_ask("127.0.0.1", "203.0.113.9")}
""")
    # nginx appended the real address last; the browser's 1.2.3.4 is ignored.
    assert out["nginx"] == {"ip": "203.0.113.9", "scheme": "https"}
    assert out["plain"]["ip"] == "203.0.113.9"


def test_direct_lan_client_cannot_set_its_ip(tmp_path):
    # LAN install: gunicorn on 0.0.0.0, the browser talks to it directly.
    out = _run(tmp_path, setup=ROUTE, body=f"""
out["lan"] = {_ask("192.168.1.50", "10.0.0.1", "https")}
""")
    assert out["lan"] == {"ip": "192.168.1.50", "scheme": "http"}


def test_trusted_proxies_can_be_configured(tmp_path):
    out = _run(tmp_path, setup=ROUTE, extra_env={"TRUSTED_PROXIES": "172.17.0.0/16"}, body=f"""
out["docker"] = {_ask("172.17.0.5", "198.51.100.7")}
out["loopback"] = {_ask("127.0.0.1", "198.51.100.7")}
""")
    assert out["docker"]["ip"] == "198.51.100.7"
    # Loopback is no longer trusted once the list is replaced.
    assert out["loopback"]["ip"] == "127.0.0.1"


def test_whitelist_cannot_be_bypassed_with_a_forged_header(tmp_path):
    out = _run(tmp_path, extra_env={"IP_WHITELIST": "203.0.113.9"}, body=r"""
fwd = lambda xff: c.get("/login", headers={"X-Forwarded-For": xff},
                        environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code
out["forged"] = fwd("203.0.113.9, 198.51.100.1")
out["allowed"] = fwd("198.51.100.1, 203.0.113.9")
out["direct"] = c.get("/login", headers={"X-Forwarded-For": "203.0.113.9"},
                      environ_base={"REMOTE_ADDR": "192.168.1.50"}).status_code
""")
    assert out == {"forged": 403, "allowed": 200, "direct": 403}
