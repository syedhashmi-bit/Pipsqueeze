# CLAUDE.md

## Session Start

Always read CONTEXT.md and MEMORY.md at the start of every session 
before making any changes

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

PipSqueeze — a self-hosted WireGuard VPN management dashboard, like Tailscale but self-hosted — manages peers on a MikroTik router via the RouterOS API. Built in Python/Flask, deployed on a Ubuntu VPS via gunicorn + nginx.

## Deployment

- **Service**: `pipsqueeze` (gunicorn systemd unit; created by `install.sh`). Legacy/existing deployments may still use the old unit name `vpn-dashboard` — check with `systemctl list-units | grep -E 'pipsqueeze|vpn-dashboard'` and use whichever exists.
- **Project path**: `/var/www/pipsqueeze`
- **VPS**: any Ubuntu host (domain, hosting provider, and VM size are deployer's choice)
- **Install modes** (`install.sh` asks first): 1 public server + domain → gunicorn `127.0.0.1:<port>` behind nginx + certbot, Google optional; 2 home/office LAN, no domain → gunicorn `0.0.0.0:<port>`, `COOKIE_INSECURE=1`, optional ufw rule; 3 this machine only → `127.0.0.1:<port>`, `COOKIE_INSECURE=1`. Google sign-in is only offered in mode 1 (Google rejects plain-http and bare-IP callbacks). Test the installer with the sandbox pattern in MEMORY.md, never by running it on this host
- **LAN subnet**: configurable via `LAN_SUBNET` env var (default `192.168.88.0/24` — MikroTik factory default)
- **VPN subnet**: `10.10.0.0/24` (clients get `.2` to `.254`)
- **Python venv**: `/var/www/pipsqueeze/venv`

## Service Commands

```bash
systemctl restart pipsqueeze                       # restart after editing app.py, mikrotik_api.py, or notifications.py
systemctl status pipsqueeze                        # check running state
journalctl -u pipsqueeze -n 50 --no-pager         # view recent errors
```

> On older deployments the unit may be named `vpn-dashboard`; substitute that name if `pipsqueeze` doesn't exist.

HTML templates take effect immediately (Jinja2 reloads on request) — no restart needed.

## Python Environment

```bash
source /var/www/pipsqueeze/venv/bin/activate
python app.py                                      # run locally for testing
pip install <package>                              # install inside venv
```

## Git

```bash
git remote                                         # origin → https://github.com/syedhashmi-bit/Pipsqueeze.git
git push                                           # push to main (HTTPS, authed by the gh credential helper)
```

## Stack

- **Backend**: Python 3.12, Flask, SQLite (`vpn_dashboard.db`)
- **MikroTik**: `routeros_api` library → `mikrotik_api.py`
- **Frontend**: Jinja2 server-rendered HTML; JS only for live updates (`/peers` polls every 5s), Chart.js sparklines, Leaflet.js map
- **Auth**: Username/password + TOTP 2FA (pyotp), optional Sign in with Google (Authlib OIDC, `.env` email allowlist — same model as Protek/Othoni/RiMM), rate limiting, session timeout, IP whitelist
- **Notifications**: Discord webhook, SMTP email, Telegram bot — `notifications.py`
- **Design**: Refined dark NOC look — electric cyan, neon green, deep navy, Rajdhani + Share Tech Mono (Google Fonts). Tokens and components live in `static/css/app.css`; every admin page extends `templates/base.html`. Light theme via `<html data-theme="light">`

## Architecture

### Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask app, all routes, background monitor thread |
| `mikrotik_api.py` | RouterOS API wrapper — connect, get_peers, add/delete/rename/enable/disable peers |
| `notifications.py` | Discord/Email/Telegram sender; no Flask dependency, safe to import from both routes and thread |
| `google_auth.py` | Sign in with Google — OIDC + PKCE via Authlib, `GOOGLE_*_EMAILS` allowlist → role; no Flask import at module level |
| `templates/base.html` | Shared layout for every admin page: head, app bar + main nav, theme toggle, flash messages, `#toast`, SW registration. Blocks: `title`, `head`, `content`, `modals`, `scripts` |
| `static/css/app.css` | Design tokens (dark + light) and shared components: `.btn*`, `.panel*`, `.chip`, `.tiles/.tile`, tables, form fields, `.segmented`, `.modal*`, `.menu`, `.toast`, `.empty-state` |
| `templates/settings.html` | Settings hub (`/settings`): links to Alerts, Users, API keys, Provisioning, Import, Activity log, exports; danger zone (typed `RESET`) |
| `templates/error.html` | Styled 404 / 500 pages |
| `templates/login.html` | Login page (standalone, not on base.html): Google button, or password + TOTP when Google is off |
| `templates/index.html` | Clients dashboard — full-width client list with ⋯ row menus, New client / edit / rename / clone dialogs, status tiles |
| `templates/wireguard.html` | Live peers page — ping, uptime, traffic sparklines, expand rows |
| `templates/security.html` | Login audit trail, locked IPs, IP whitelist info |
| `templates/notifications.html` | Discord/Email/Telegram alert channel settings |
| `templates/report.html` | Weekly digest — top users, uptime leaders, expiry warnings |
| `templates/map.html` | World map of client locations (Leaflet.js + OpenStreetMap) |
| `templates/portal.html` | Client self-serve portal (token-based, no login required) |
| `templates/logs.html` | Full paginated activity log |
| `templates/blocked.html` | IP whitelist rejection page (403) |
| `clients/` | WireGuard `.conf` files per client — **never read** |
| `qr_codes/` | QR code PNGs per client — **never read** |
| `vpn_dashboard.db` | SQLite DB — **never read** (contains credentials) |
| `.env` | All secrets — **never read** |
| `CONTEXT.md` | Architecture decisions, background, known quirks |
| `MEMORY.md` | Running log of features built, bugs fixed, and pending work |

### Background Monitor Thread

`_monitor_loop()` runs every 30 seconds in a daemon thread:
1. Polls MikroTik for all peers
2. Records traffic **deltas** (not cumulative) using `_prev_traffic` dict
3. Records connect/disconnect events and sends notifications
4. Records uptime status to `uptime_log` table
5. Pings online peers and records latency to `ping_history` table
6. Auto-disables expired clients on MikroTik + DB
7. Sends weekly digest email on configured weekday (`WEEKLY_DIGEST_DAY` env var)

The `_prev_states` and `_prev_traffic` module-level dicts must never be reset — they track state across loop iterations.

Only **one process** runs the monitor: gunicorn (`-w 3` in production) imports `app.py` once per worker, so `_claim_monitor_lock()` takes a non-blocking `fcntl.flock` on `.monitor.lock` (next to the DB, in the cwd) and only the winner starts the thread. The kernel releases the lock when the owner exits, and the replacement worker takes over. Never start the monitor elsewhere without the lock: duplicates multiply `total_rx`/`total_tx` and every alert. In-memory monitor state (`_peer_endpoints`, `_geo_cache`) only exists in the owner worker, so request handlers must not depend on it.

### Traffic Delta Tracking

MikroTik resets RX/TX counters on reboot. The monitor thread stores previous values in `_prev_traffic` and adds only the difference to `total_rx`/`total_tx` in the DB. `max(0, delta)` guards against negative values on router reboot.

### LAN Access Mode

Each client has an `access_mode` that controls what `AllowedIPs` is written into their `.conf` file:

| Mode | Value | AllowedIPs in .conf |
|------|-------|---------------------|
| `internet` | default | `0.0.0.0/0` |
| `lan` | LAN only | `192.168.88.0/24` |
| `full` | Full access | `0.0.0.0/0, 192.168.88.0/24` |

`AllowedIPs` is **client-side config only** — no MikroTik change is needed when mode changes. Regenerate the `.conf` and QR code when mode is updated via edit modal.

### Portal Tokens

Each client has a unique `portal_token`. Clients visit `/portal/<token>` to download their config/QR code without logging in. Rotating the token immediately invalidates the old link.

### Security

- Rate limiting: `MAX_LOGIN_ATTEMPTS` failed attempts trigger lockout for `LOCKOUT_MINUTES`
- Session timeout: `SESSION_TIMEOUT_MIN` inactivity → auto-logout
- IP whitelist: `IP_WHITELIST` env var (comma-separated); blank = allow all
- Client IP: `get_client_ip()` returns `request.remote_addr` only — **never read `X-Forwarded-For` directly** (nginx appends to it, so its first entry is whatever the browser sent; reading it let anyone fake their IP past the whitelist/lockout). `_trusted_proxy_fix()` applies `ProxyFix(x_for=1, x_proto=1, x_host=1)` only when the socket peer is in `TRUSTED_PROXIES` (default `127.0.0.1,::1`), so a LAN install with gunicorn on `0.0.0.0` and no nginx can't be fooled either
- Google sign-in (optional): `/auth/google` → `/auth/google/callback`. Access = `GOOGLE_ADMIN_EMAILS` / `GOOGLE_VIEWER_EMAILS` in `.env`; Google users get **no** `admin_users` row (session `auth_source="google"`, `username` = email) and `session_source_error()` re-checks the allowlist on every request in `login_required`/`admin_required`. No TOTP prompt for Google users. **While Google is on (`_google is not None`) it is the only login**: `/login` renders just the button, a password POST is refused before credentials are checked, and non-Google sessions are cleared (`?error=password_off`). With Google unconfigured (or Authlib missing) the password + TOTP form is the login, so clearing `GOOGLE_CLIENT_ID` + restart is the recovery path. `APP_USERNAME`/`APP_PASSWORD`/`TOTP_SECRET` are only required at startup while `_google is None` (check sits just before `init_db()`); with Google on they may be absent and `init_db()` seeds no default admin — never reintroduce an `admin`/`changeme` fallback. Refused accounts count toward the IP lockout. Pass `aud` in `claims_options` — Authlib skips the audience check otherwise
- All security events logged to `login_audit` table

### Pages & API Routes

| Route | Purpose |
|-------|---------|
| `/` | Dashboard — create clients, manage list, stats |
| `/wireguard` | Live MikroTik peer status, ping, uptime |
| `/security` | Login audit, locked IPs, IP whitelist |
| `/notifications` | Discord/Email/Telegram alert settings |
| `/weekly-report` | Weekly usage and uptime summary |
| `/map` | Geographic client distribution (Leaflet.js) |
| `/logs` | Full paginated activity log |
| `/settings` | Settings hub + danger zone (reset DB / reset all, both require `confirm=RESET`) |
| `/auth/google` | Start Sign in with Google (redirects to Google) |
| `/auth/google/callback` | Google OIDC callback — verifies ID token, checks allowlist, logs in |
| `/portal/<token>` | Client self-serve (no login required) |
| `/peers` | JSON — live peer list from MikroTik |
| `/api/sys` | JSON — CPU, RAM, disk, uptime |
| `/api/mt-health` | JSON — MikroTik API reachability |
| `/api/traffic/<client>` | JSON — RX/TX history |
| `/api/ping/<client>` | JSON — ping latency history |
| `/api/uptime/<client>` | JSON — 7-day uptime percentage |
| `/api/events/<client>` | JSON — connect/disconnect events |
| `/api/send-digest` | POST — trigger weekly digest email immediately |

## Database Schema

- `clients` — VPN clients (name, ip, tags, notes, location, lat, lon, expires_at, disabled, last_seen, total_rx, total_tx, portal_token, access_mode, created_at)
- `activity_logs` — All admin actions with timestamps
- `traffic_history` — RX/TX snapshots every 30s per peer
- `peer_events` — Connect/disconnect events
- `ping_history` — Latency per peer every 30s
- `uptime_log` — Online/offline status every 30s (used for 7-day rolling uptime %)
- `notifications` — Alert channel settings (one row: Discord/Email/Telegram config + per-event toggles)
- `login_attempts` — Rate limiting records per IP
- `login_audit` — Every login attempt with IP, username, result, reason

**DB migrations**: new columns must be added to both the `CREATE TABLE` statement and the `init_db()` migration block (`ALTER TABLE ... ADD COLUMN` guarded by PRAGMA column check).

## Environment Variables (.env)

```
# App
SECRET_KEY=
APP_USERNAME=
APP_PASSWORD=
TOTP_SECRET=

# WireGuard server
SERVER_PUBLIC_KEY=
SERVER_IP=
SERVER_PORT=
CLIENT_DNS=

# MikroTik API
MT_HOST=
MT_USERNAME=
MT_PASSWORD=
MT_PORT=
MT_WIREGUARD_INTERFACE=

# Security (optional — have defaults)
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=15
SESSION_TIMEOUT_MIN=30
IP_WHITELIST=

# Google sign-in (optional — all blank = password + 2FA only)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_ADMIN_EMAILS=
GOOGLE_VIEWER_EMAILS=

# Notifications (optional)
WEEKLY_DIGEST_DAY=monday
```

## Coding Rules

- **Never read `.env`, `vpn_dashboard.db`, `clients/`, or `qr_codes/`** — live credentials and private WireGuard keys
- Always restart the service after editing `app.py`, `mikrotik_api.py`, or `notifications.py`
- MikroTik peer IDs may be `id` or `.id` depending on API version — always use `get_peer_id()` helper
- Last handshake from MikroTik is formatted `1h2m30s` — parsed with regex; peer is "Online" if handshake < 120s ago
- Flash messages are rendered by `base.html` on every page (`flash(msg, "error")` or text containing error/failed/required styles them as errors) — don't add per-page flash code
- The wireguard page sorts (peer row, expand row) as bonded pairs — keep this invariant when touching sort logic
- All new DB columns go in both `CREATE TABLE` and the `init_db()` migration block
- UI: new admin pages `{% extends "base.html" %}` and use `app.css` classes and tokens — no hex colours (they break light mode), no glows/text-shadow/scanline or grid backgrounds/entrance animations, no `// ` prefixes or ALL-CAPS labels, no emoji icons. Monospace (`.mono` / `var(--mono)`) only for data: IPs, keys, byte counts, timestamps. Byte counts via the `bytes` Jinja filter (`fmt_bytes`). Keep the cyan/green/navy + Rajdhani/Share Tech Mono identity
- External scripts/styles must be allowed by the CSP in `_security_headers` (`tests/test_pages.py` enforces this); the service worker must never `respondWith` for other origins (inside the worker, fetch runs under its own CSP and silently blocks fonts/CDNs). Bump `CACHE_VERSION` in `static/sw.js` when static assets change
- Map tiles are OpenStreetMap's (`tile.openstreetmap.org`) with `referrerPolicy: 'strict-origin-when-cross-origin'` and visible attribution — OSM blocks Referer-less requests and the site-wide Referrer-Policy is `same-origin`. CARTO basemaps need an API key now
- VSCode flags Jinja2 `{{ }}` inside `<script>` tags as JS errors — these are false positives, code works fine
- Geocoding uses OpenStreetMap Nominatim (browser-side JS, no API key, 1 req/sec rate limit)
- After any significant session, update `MEMORY.md` with what was changed
