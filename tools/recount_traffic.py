"""
Recount each client's total_rx / total_tx from traffic_history.

Until 2026-10-28 every gunicorn worker ran its own monitor thread, so on a
multi-worker deployment (production runs `gunicorn -w 3`) each 30 s delta was
added to total_rx / total_tx once per worker. traffic_history keeps every raw
counter sample those monitors took, so the true total is the sum of the
counter's increases between consecutive samples: the monitor's own
max(0, delta) rule, applied once.

Totals are only ever lowered: a client whose recount is not below its stored
total (single-worker installs, or no history) is left alone.

    venv/bin/python tools/recount_traffic.py            # dry run: stored → recounted
    venv/bin/python tools/recount_traffic.py --apply    # back up the DB, then write

Run it from the project directory. Stop the service while applying
(systemctl stop pipsqueeze) so the monitor can't add a delta mid-recount.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

# Sum of positive steps between consecutive samples of each client's counters.
RECOUNT_SQL = """
    WITH steps AS (
        SELECT client,
               rx - LAG(rx) OVER w AS drx,
               tx - LAG(tx) OVER w AS dtx
        FROM traffic_history
        WINDOW w AS (PARTITION BY client ORDER BY id)
    )
    SELECT client,
           SUM(CASE WHEN drx > 0 THEN drx ELSE 0 END),
           SUM(CASE WHEN dtx > 0 THEN dtx ELSE 0 END)
    FROM steps GROUP BY client
"""


def fmt(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def plan(conn):
    """[(name, stored_rx, stored_tx, new_rx, new_tx)] for clients that change."""
    recount = {c: (rx or 0, tx or 0) for c, rx, tx in conn.execute(RECOUNT_SQL)}
    changes = []
    for name, rx, tx in conn.execute("SELECT name, total_rx, total_tx FROM clients ORDER BY name"):
        rx, tx = rx or 0, tx or 0
        if name not in recount:
            continue
        new_rx, new_tx = recount[name]
        if new_rx + new_tx < rx + tx:
            changes.append((name, rx, tx, min(rx, new_rx), min(tx, new_tx)))
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--db", default="vpn_dashboard.db", help="path to the database (default: ./vpn_dashboard.db)")
    ap.add_argument("--apply", action="store_true", help="write the recounted totals (after a backup)")
    args = ap.parse_args(argv)
    if not os.path.exists(args.db):
        sys.exit(f"{args.db} not found — run this from the project directory or pass --db.")

    conn = sqlite3.connect(args.db, timeout=30)
    conn.isolation_level = None
    if args.apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{args.db}.bak-{stamp}"
        old_umask = os.umask(0o077)
        try:
            with sqlite3.connect(backup) as dst:
                conn.backup(dst)
        finally:
            os.umask(old_umask)
        print(f"Backed up {args.db} → {backup}")
        conn.execute("BEGIN IMMEDIATE")   # hold off the monitor between read and write

    changes = plan(conn)
    if not changes:
        print("Nothing to change: no client's total is above its recount.")
        if args.apply:
            conn.execute("ROLLBACK")
        return 0

    width = max(len(c[0]) for c in changes)
    print(f"{'client':<{width}}  {'stored (rx / tx)':>25}  {'recounted (rx / tx)':>25}  ratio")
    for name, rx, tx, new_rx, new_tx in changes:
        ratio = (rx + tx) / (new_rx + new_tx) if new_rx + new_tx else float("inf")
        print(f"{name:<{width}}  {fmt(rx) + ' / ' + fmt(tx):>25}  "
              f"{fmt(new_rx) + ' / ' + fmt(new_tx):>25}  {ratio:4.1f}x")

    if not args.apply:
        print(f"\nDry run: {len(changes)} client(s) would change. Re-run with --apply to write.")
        return 0
    conn.executemany("UPDATE clients SET total_rx=?, total_tx=? WHERE name=?",
                     [(new_rx, new_tx, name) for name, _, _, new_rx, new_tx in changes])
    conn.execute("COMMIT")
    print(f"\nUpdated {len(changes)} client(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
