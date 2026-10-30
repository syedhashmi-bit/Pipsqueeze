"""
install.sh writes every answer into .env through envq(); the app reads it back
with python-dotenv (interpolate=False). Whatever the user typed must come back
unchanged, or a password with a space, #, quote or ${...} silently stops
working.

Run with: pytest tests/test_installer_env.py -v
"""

import os
import subprocess

from dotenv import dotenv_values

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pull just the helpers out of install.sh (running the whole script would
# start installing), then print one KEY=<envq value> line per argument.
DRIVER = r"""
eval "$(sed -n '/^ENV_NEEDS_QUOTES=/p;/^env_unsafe()/p;/^envq()/,/^}/p' "$1/install.sh")"
shift
i=0
for v in "$@"; do printf 'V%d=%s\n' "$i" "$(envq "$v")"; i=$((i+1)); done
echo "AFTER=still-here"
"""

VALUES = [
    "plain", "pa ss #word", "it's", 'say "hi"', "x${HOME}y", "$HOME", "a#b",
    " lead", "tail ", "'quoted'", "back\\slash", "trail\\", "\\\\double",
    "a\\'b", "mixed ' \"q\" \\ ${X} #", "Hi0g2rkYi17qDPUMQP1q+9Y2PQG0sowAAL2urZaugxk=",
    "a@b.com,c@d.com", "",
]


def test_every_answer_survives_the_round_trip(tmp_path):
    out = subprocess.run(["bash", "-c", DRIVER, "driver", REPO, *VALUES],
                         capture_output=True, text=True, check=True).stdout
    env = tmp_path / ".env"
    env.write_text("HOME=/root\n" + out)
    got = dotenv_values(env, interpolate=False)
    assert [got.get(f"V{i}") for i in range(len(VALUES))] == VALUES
    # A bad line would have swallowed the lines after it.
    assert got["AFTER"] == "still-here"


def test_unrepresentable_values_are_refused():
    check = r"""
eval "$(sed -n '/^ENV_NEEDS_QUOTES=/p;/^env_unsafe()/p' "$1/install.sh")"
shift
for v in "$@"; do env_unsafe "$v" && echo refuse || echo ok; done
"""
    values = ["sp ace\\", "it's\\", "trail\\", "ok\\ x"]
    out = subprocess.run(["bash", "-c", check, "check", REPO, *values],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out == ["refuse", "refuse", "ok", "ok"]


def test_app_loads_env_without_expanding_variables():
    # mikrotik_api.py is imported first, so both loads must opt out.
    for name in ("app.py", "mikrotik_api.py"):
        src = open(os.path.join(REPO, name)).read()
        assert "load_dotenv(interpolate=False)" in src, name
        assert "load_dotenv()" not in src, name
