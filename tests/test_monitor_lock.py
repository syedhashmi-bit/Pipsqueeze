"""
Monitor-thread election tests: only one process per deployment may run
_monitor_loop(). gunicorn imports app.py once per worker (-w 3 in production),
and each copy used to start its own monitor, multiplying traffic totals and
alerts.

Each test process imports app.py from *this* checkout inside a shared temp dir
(throwaway DB, no .env read, MikroTik pointed at a closed port) and reports
whether it started the monitor, then holds still until the test closes its
stdin.

Run with: pytest tests/test_monitor_lock.py -v
"""

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LAUNCHER = r"""
import sys
import dotenv
dotenv.load_dotenv = lambda *a, **k: False   # never read a real .env
sys.path.insert(0, sys.argv[1])
import app
print("OWNER" if app.monitor_thread is not None else "SKIPPED", flush=True)
sys.stdin.read()   # hold the process, and any lock, until the test lets go
"""

ENV = {
    "SECRET_KEY":        "test-secret-key-for-monitor-lock-tests",
    "APP_USERNAME":      "breakglass",
    "APP_PASSWORD":      "breakglass-pass-xyz",
    "TOTP_SECRET":       "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
    "COOKIE_INSECURE":   "1",
    "AUTO_CLEANUP_DAYS": "0",
    # Nothing listens here, so the monitor just fails to connect and sleeps.
    "MT_HOST":           "127.0.0.1",
    "MT_PORT":           "9",
}


def _spawn(workdir):
    """Start one app process in `workdir`; return (proc, "OWNER"|"SKIPPED")."""
    p = subprocess.Popen(
        [sys.executable, "-I", "-c", LAUNCHER, REPO],
        cwd=workdir, env={**os.environ, **ENV}, text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    seen = []
    for line in p.stdout:
        seen.append(line)
        if line.strip() in ("OWNER", "SKIPPED"):
            return p, line.strip()
    p.wait()
    raise RuntimeError("app process exited before reporting:\n" + "".join(seen[-30:]))


def _stop(procs):
    for p in procs:
        if p.poll() is None:
            p.stdin.close()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def test_only_one_process_starts_the_monitor(tmp_path):
    procs = []
    try:
        results = []
        for _ in range(3):
            p, who = _spawn(tmp_path)
            procs.append(p)
            results.append(who)
        assert results == ["OWNER", "SKIPPED", "SKIPPED"]
        # The lock file names the owner, for anyone debugging a live box.
        assert (tmp_path / ".monitor.lock").read_text().strip() == str(procs[0].pid)
    finally:
        _stop(procs)


def test_monitor_passes_to_next_process_when_owner_exits(tmp_path):
    procs = []
    try:
        owner, who = _spawn(tmp_path)
        procs.append(owner)
        assert who == "OWNER"
        other, who = _spawn(tmp_path)
        procs.append(other)
        assert who == "SKIPPED"

        # The owner dies (gunicorn would then spawn a replacement worker).
        _stop([owner])
        replacement, who = _spawn(tmp_path)
        procs.append(replacement)
        assert who == "OWNER"
        assert (tmp_path / ".monitor.lock").read_text().strip() == str(replacement.pid)
    finally:
        _stop(procs)


def test_separate_deployments_dont_block_each_other(tmp_path):
    # The lock is per working directory (next to vpn_dashboard.db), so a test
    # instance in another directory never steals production's monitor.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    procs = []
    try:
        for d in ("a", "b"):
            p, who = _spawn(tmp_path / d)
            procs.append(p)
            assert who == "OWNER"
    finally:
        _stop(procs)
