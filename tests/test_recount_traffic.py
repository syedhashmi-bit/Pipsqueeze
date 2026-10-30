"""
tools/recount_traffic.py and the weekly report's per-client usage, on a
synthetic database where three monitor threads sampled the same counters (as
every gunicorn worker did before the monitor lock).

Run with: pytest tests/test_recount_traffic.py -v
"""

import os
import sqlite3
import subprocess
import sys

from tests.test_pages import _run

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "recount_traffic.py")

# The router's counters for "laptop", one value per 30 s poll. 1500 → 200 is a
# router reboot; the monitor's max(0, delta) drops that step.
LAPTOP_RX = [1000, 1400, 1500, 200, 900, 2000]
LAPTOP_TX = [10, 50, 90, 5, 40, 80]


def _simulate(db, name, rxs, txs, monitors):
    """Write what `monitors` monitor threads would have: one history row each
    per poll, and each adding its own delta to the totals."""
    prev = [None] * monitors
    total_rx = total_tx = 0
    for rx, tx in zip(rxs, txs):
        for m in range(monitors):
            p_rx, p_tx = prev[m] or (rx, tx)
            total_rx += max(0, rx - p_rx)
            total_tx += max(0, tx - p_tx)
            prev[m] = (rx, tx)
            db.execute("INSERT INTO traffic_history (client, rx, tx, recorded_at) VALUES (?,?,?,?)",
                       (name, rx, tx, "2026-10-01 00:00:00 UTC"))
    db.execute("INSERT INTO clients (name, total_rx, total_tx) VALUES (?,?,?)", (name, total_rx, total_tx))


def _db(tmp_path):
    path = tmp_path / "vpn_dashboard.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, total_rx INTEGER DEFAULT 0, total_tx INTEGER DEFAULT 0)")
    db.execute("CREATE TABLE traffic_history (id INTEGER PRIMARY KEY AUTOINCREMENT, client TEXT, rx INTEGER, tx INTEGER, recorded_at TEXT)")
    _simulate(db, "laptop", LAPTOP_RX, LAPTOP_TX, monitors=3)
    _simulate(db, "phone", [0, 100, 300], [0, 10, 30], monitors=1)   # already right
    db.execute("INSERT INTO clients (name, total_rx, total_tx) VALUES ('new-client', 5, 5)")  # no history
    db.commit()
    db.close()
    return path


def _totals(path):
    with sqlite3.connect(path) as db:
        return {n: (rx, tx) for n, rx, tx in db.execute("SELECT name, total_rx, total_tx FROM clients")}


def _tool(path, *args):
    return subprocess.run([sys.executable, "-I", TOOL, "--db", str(path), *args],
                          capture_output=True, text=True, check=True).stdout


def test_dry_run_reports_but_changes_nothing(tmp_path):
    path = _db(tmp_path)
    before = _totals(path)
    assert before["laptop"] == (3 * 2300, 3 * 155)   # (400+100+700+1100) and (40+40+35+40), three times
    out = _tool(path)
    assert "laptop" in out and "3.0x" in out and "Dry run: 1 client(s)" in out
    assert _totals(path) == before
    assert not list(tmp_path.glob("*.bak-*"))


def test_apply_recounts_once_and_keeps_a_backup(tmp_path):
    path = _db(tmp_path)
    before = _totals(path)
    out = _tool(path, "--apply")
    assert "Updated 1 client(s)" in out
    after = _totals(path)
    assert after["laptop"] == (2300, 155)
    # Single-monitor and history-less clients are left alone.
    assert after["phone"] == before["phone"] and after["new-client"] == (5, 5)
    [backup] = tmp_path.glob("vpn_dashboard.db.bak-*")
    assert _totals(backup) == before
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    # Running it again finds nothing left to fix.
    assert "Nothing to change" in _tool(path, "--apply")


def test_weekly_report_counts_increases_not_samples(tmp_path):
    out = _run(tmp_path, r"""
from datetime import timedelta
db = sqlite3.connect("vpn_dashboard.db")
t = lambda m: (datetime.utcnow() - timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S UTC")
for i, (rx, tx) in enumerate([(1000, 10), (1400, 50), (1500, 90), (200, 5), (900, 40), (2000, 80)]):
    db.execute("INSERT INTO traffic_history (client, rx, tx, recorded_at) VALUES ('laptop', ?, ?, ?)", (rx, tx, t(60 - i)))
db.execute("INSERT INTO traffic_history (client, rx, tx, recorded_at) VALUES ('laptop', 999999, 9, ?)", (t(60 * 24 * 8),))
db.commit(); db.close()
with A.app.app_context():
    out["top"] = A.build_weekly_digest()["top_users"]
""")
    # 400+100+700+1100 B received, 40+40+35+40 B sent; the 8-day-old sample is outside the week.
    assert out["top"] == [{"client": "laptop", "rx": "2.25 KB", "tx": "155 B", "total": "2.40 KB"}]
