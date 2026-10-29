# MEMORY.md — PipSqueeze Running Decision Log

This file tracks what has been built, what was fixed, and important decisions made. Update this after significant changes so future Claude Code sessions have full context.

---

## 2026-10-28 — Sign in with Google (same model as Protek / Othoni / RiMM)

- New `google_auth.py`, ported from Protek's Flask module (`/var/www/Protek/google_auth.py`): OIDC authorization-code flow via Authlib, `state` in the signed session cookie + `nonce` + PKCE S256, account chooser every time. Callback pins `iss` (both Google forms) **and `aud`** (Authlib only checks audience when `claims_options` names it — Protek's version passes only `iss`), requires `email_verified is True`.
- Access is env-only: `GOOGLE_ADMIN_EMAILS` / `GOOGLE_VIEWER_EMAILS` (comma-separated, case-insensitive, admin wins if on both) + `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`. Button shows only when all of client ID, secret and ≥1 email are set; a half-filled config logs `[google] sign-in disabled: …` at boot and shows on Admin → Users.
- Google users get **no `admin_users` row** (emails can't collide with local usernames — those are `[a-zA-Z0-9_.-]`). Session carries `auth_source="google"`, `username`=email. New `google_session_ok()` in `login_required`/`admin_required` re-reads the allowlist every request → removing/demoting an email + restart lands on the next click.
- No TOTP prompt for Google users (Google 2-Step Verification is the second factor). Password + TOTP form folds under "// use password + 2FA code instead" as break-glass; a failed POST keeps it unfolded. **Superseded the same day** — see "Google-only login" below.
- `/auth/google` + callback go through the same IP whitelist + lockout gates. `not_allowed`/`unverified` count toward lockout and fire the `login_failure`/`login_locked` alerts (factored out of `login()` into `notify_login_failure()`); cancelled/expired round trips don't. Audit reasons: `Google` on success, `Google: <code>` on failure. `/login?error=` only renders codes from `google_auth.ERROR_MESSAGES`.
- Admin → Users: read-only GOOGLE SIGN-IN panel (ON/OFF/MISCONFIGURED, exact redirect URI from `url_for(..., _external=True)`, allowlist table; never shows the secret).
- `login.html` body `overflow: hidden` → `overflow-x: hidden`: the unfolded form is ~900px tall and was unscrollable on short screens.
- `requirements.txt`: + `Authlib==1.7.2`, `requests==2.34.2` (+ certifi, charset-normalizer, idna, urllib3). App still boots without Authlib — Google just stays off.
- `install.sh` optionally prompts for the four `GOOGLE_*` values; `.env.example`, README (setup + config table), CLAUDE.md updated.
- `tests/test_google_auth.py` (20 tests): in-process, offline, throwaway DB, `load_dotenv` stubbed; Google simulated with a joserfc RSA key + stubbed token exchange, so Authlib's real signature/nonce/state/expiry checks run. Each refusal test was checked to fail for its own reason.
- Prod venv now has Authlib (installed 2026-10-28 from this branch's `requirements.txt`; also pinned `joserfc==1.7.7`, Authlib's JOSE backend). Merged into local `main` on 2026-10-28 (not pushed); service still stopped on purpose.

### Same day — Google-only login (password + 2FA removed while Google is on)

- Owner asked to drop the password + 2FA fallback. While Google sign-in is active (`_google is not None`), `/login` shows only the Google button; a password POST is refused **before** credentials are checked (no oracle, no lockout count; audit reason `Password login off (Google only)`); `session_source_error()` (replaces `google_session_ok()`) also clears any non-Google session → `/login?error=password_off`.
- Kept on purpose: if Google isn't configured or Authlib is missing, the password + TOTP form is the login. Fresh installs don't need Google, and **clearing `GOOGLE_CLIENT_ID` + restart is the recovery path** if Google breaks. (Startup requirement for `APP_USERNAME`/`APP_PASSWORD`/`TOTP_SECRET` relaxed right after — see next entry.)
- Admin → Users Google panel warns that the listed local users can't sign in while Google is on. Their CHANGE PW / RESET 2FA buttons still work (they matter again if Google is turned off).
- API keys (`/api/v1/*`) are unaffected.
- Tests: login page has no password/code inputs; valid password + TOTP POST refused; pre-existing password session signed out; with Google off the password login works end to end. The three Google-only tests fail against the previous commit.

### Same day — 502 after going Google-only: startup demanded password creds

- Owner removed `APP_USERNAME`/`APP_PASSWORD` (Google-only), started the service → every worker raised `RuntimeError: APP_USERNAME and APP_PASSWORD must be set` at import, gunicorn halted, nginx served 502.
- Fix: the creds check moved from the top of `app.py` to STARTUP (just before `init_db()`) and only runs while `_google is None`. `init_db()` now seeds the env admin only when both creds are set — the old `os.getenv(..., "admin"/"changeme")` fallbacks are gone, so a Google-only boot can't plant a known-password admin that would go live if Google were switched off.
- Recovery path is now: clear `GOOGLE_CLIENT_ID` **and** set the three creds, then restart (docs, `.env.example`, Admin → Users panel updated).
- Tests (fresh processes): Google-only boot with no creds starts and seeds 0 admins; Google off without creds refuses; half-configured Google still needs creds; creds set → admin seeded. The Google-only boot test reproduces the journal error against the previous commit.

### Same day — app log lines only reached the journal on worker exit

- Under systemd stdout is a pipe, so Python block-buffered every `print()` (`[monitor]`, `[google]`, `[vault]`, `[firewall]`); they only showed in `journalctl` when a worker exited. `app.py` now calls `sys.stdout.reconfigure(line_buffering=True)` right after its imports. It covers every module's prints and doesn't depend on the unit file (no `PYTHONUNBUFFERED` needed). Test: `test_log_lines_reach_a_pipe_while_the_process_runs` (fails against the previous commit).

### 2026-10-29 — UI redesign on a shared base template (+ three live bugs)

- New `templates/base.html` + `static/css/app.css`: one app bar/nav (Clients, Peers, Map, Reports, Security, Settings) on every admin page, light/dark toggle for all pages (`html[data-theme]`, localStorage `theme`, set before first paint), flash messages rendered for every page (the dashboard used to never show them), shared `#toast`. All 12 admin templates converted (`{% extends "base.html" %}`); login/portal/provision/blocked stay standalone.
- Removed the "generated" look: animated grid/scanlines/glow blobs/data streams, text glows, gradient hairlines, `// ` prefixes, ALL-CAPS labels, emoji icons. Monospace only for data. Contrast: `--muted` #3d6070 (2.9:1) → #62879a (4.9:1); light theme `--muted` #5d7182, yellow #8a6400.
- Dashboard: full-width client list; New client form moved into a dialog (N shortcut); row actions = Download, Enable/Disable, ⋯ menu (Edit, Rename, Clone, Copy/Rotate portal link, Regenerate keys, Delete) with fixed-position popover; one row of status tiles; bytes via new `bytes` Jinja filter; notification bell dropped (duplicated the expiry banner); viewers no longer see admin-only controls.
- New `/settings` hub (admin tools that crowded the top bar) with a danger zone: `/reset-db` and `/reset-all` now require `confirm=RESET` server-side.
- Styled 404/500 pages (`error.html`; JSON for `/api/*` and `/peers`).
- Bug: `/wireguard` 500'd whenever MikroTik was unreachable (no try around connect) → now renders with an error banner; `/peers` returns 503 JSON and the page shows the banner instead of toasting every 5s.
- Bug (live since the CSP hardening pass): Leaflet and Chart.js load from `cdnjs.cloudflare.com`, which the CSP didn't allow → `/map` blank and peer charts empty. Added cdnjs to script-src/style-src. Then CARTO tiles turned out to need an API key → switched to OpenStreetMap tiles (+ `referrerPolicy`, attribution, CSS invert for dark theme).
- Bug: the service worker proxied every GET including other origins; inside the worker fetch runs under the worker's CSP (connect-src lacks Google Fonts/CDNs), so fonts, Leaflet and Chart.js failed on every page after the first visit. SW now ignores cross-origin requests; `CACHE_VERSION` → 2026-10-29a.
- Tests: `tests/test_pages.py` (8, fresh-process): one nav with the right active section on every page, Peers survives a router outage, styled 404/500, typed reset confirmation, flashes on the dashboard, admin vs viewer controls + create-form field contract, CSP allows every external host templates load, SW leaves other origins alone. Old suites' strings updated (Keyboard shortcuts, Enroll 2FA, Google sign-in).
- Process note: page conversions were split between two helper agents; both hit the usage limit but had finished their files — each page was verified with a render check and a JS-id check afterwards.

### Same day — monitor thread ran once per gunicorn worker

- The live unit runs `gunicorn -w 3` without `--preload`, so all three workers imported `app.py` and each started `_monitor_loop()` with its own `_prev_traffic`/`_prev_states`. Every 30s the same delta was added to `total_rx`/`total_tx` three times. `uptime_log`/`ping_history`/`traffic_history` got 3 rows. Connect/disconnect/expiry/quota alerts, expiry reminders, the weekly digest and auto-cleanup ran three times. On each restart every worker re-sent a "connected" alert per online peer.
- Fix: `_claim_monitor_lock()` at startup — non-blocking `fcntl.flock` on `.monitor.lock` (cwd, next to the DB; PID written inside; gitignored), same pattern as Protek's `.poller.lock`. Only the lock holder starts the thread; the others print `[monitor] already running…`. If the lock file can't be opened it fails open (starts the monitor) rather than run with none.
- Verified with real `gunicorn -w 3`: one worker had the monitor thread; `kill -9` on it → gunicorn's replacement worker took the lock and started the monitor. `tests/test_monitor_lock.py` (3 tests) fails against the old code (every process reports OWNER).
- Trade-off: `_peer_endpoints`/`_geo_cache` now only fill in the owner worker, so the dashboard/map 🌍 fallback (only used for clients with NULL lat/lon, i.e. geolocation failed at connect time) shows only on requests served by that worker. The monitor's DB lat/lon write on connect is unchanged. Persisting `endpoint_ip` to `clients` would remove this if it ever matters.
- Historic `total_rx`/`total_tx` and row counts from before this fix are inflated (likely ~3×) and were not corrected.

---

## 2026-06-05 — Easy install + lightweight pass

- Added `install.sh`: guided, re-runnable installer. Checks prereqs (Python 3.10+, python3-venv, optional nginx/certbot/wireguard-tools), builds venv, auto-generates `SECRET_KEY`/`TOTP_SECRET`, prompts for required `.env` values (passwords hidden), writes `.env` at 0600, renders the 2FA QR in-terminal via `qrcode.print_ascii`, and optionally creates the `pipsqueeze` systemd unit + nginx site + certbot HTTPS. Backs up existing `.env`, never clobbers the service without a prompt.
- Slimmed assets: `static/logo.png` and `static/og-image.png` re-encoded from 1024×1024/404KB to 512×512/13KB each (palette PNG, transparency kept). 512px chosen to stay valid as the PWA maskable icon. og-image is otherwise unreferenced in templates. Deleted stray untracked root `app.png`.
- Service name standardized to `pipsqueeze` in README + CLAUDE.md; legacy deployments may still run the old `vpn-dashboard` unit (noted in both docs).
- README: new Quick Start + "Quick install (recommended)" section; manual 9 steps kept as fallback.
- `.gitignore`: added `.env.backup.*`.
- Did NOT refactor `app.py` (3000-line monolith) — out of scope for an install/lightweight pass.

---

## Current State (as of May 2026)

### Features Implemented
- [x] WireGuard peer management via MikroTik RouterOS API
- [x] Client creation (generates WireGuard keys, config file, QR code)
- [x] Client list with search, tag filtering, pagination (20/page)
- [x] Client tags (comma-separated, filter buttons with counts)
- [x] Client notes field
- [x] Client expiry date with auto-disable via monitor thread
- [x] Client location (name + lat/lon) with Nominatim geocode lookup
- [x] Enable/disable clients from dashboard
- [x] Rename client (updates DB, MikroTik comment, files)
- [x] Clone client (new keys + IP, inherits tags/notes/expiry/location)
- [x] Delete client (removes from MikroTik + DB + files)
- [x] Bulk actions (enable/disable/delete multiple clients)
- [x] Regenerate WireGuard keys (keeps same IP)
- [x] Portal token per client — self-serve download page at `/portal/<token>`
- [x] Rotate portal token (invalidates old link)
- [x] 2FA login (TOTP via pyotp)
- [x] Login rate limiting (configurable max attempts + lockout duration)
- [x] Session timeout (configurable inactivity limit)
- [x] IP whitelist (via `.env`, blocks non-whitelisted IPs at both login and all routes)
- [x] Login audit trail (every attempt logged with IP, username, result, reason)
- [x] Security page (`/security`) — audit log, locked IPs, unlock button
- [x] Background monitor thread (30s polling)
- [x] Traffic delta tracking (survives MikroTik reboots)
- [x] Cumulative RX/TX per client in DB
- [x] Connect/disconnect event logging
- [x] Last seen timestamp (updated when peer is online)
- [x] Ping/latency monitoring (pings online peers every 30s)
- [x] Uptime % calculation (7-day rolling window from `uptime_log` table)
- [x] Auto-disable expired clients (monitor thread checks daily)
- [x] Expiry warning banner (clients expiring within 7 days)
- [x] Notification bell with count badge
- [x] Discord webhook notifications
- [x] Email (SMTP) notifications with Gmail App Password support
- [x] Telegram bot notifications
- [x] Per-event notification toggles (connect/disconnect/expiry/new/delete/regen)
- [x] Test notification button (fires without saving)
- [x] Weekly digest email (auto-sends on configured weekday)
- [x] Weekly report page with top users, uptime leaders, expiry warnings, connection events
- [x] World map view (Leaflet.js + OpenStreetMap, dark-filtered tiles)
- [x] VPS system stats (CPU, RAM, disk bars, uptime) — live update every 10s
- [x] MikroTik API health check with recheck button
- [x] Dark/light mode toggle (saved in localStorage)
- [x] Keyboard shortcuts: N (focus create field), R (open peers), Esc (close modals)
- [x] Auto-focus on modal open
- [x] Relative timestamps ("2h ago") with full timestamp on hover
- [x] Copy IP button per client
- [x] Rotate portal link button per client
- [x] Client search across name, IP, notes, tags, location
- [x] Export to CSV
- [x] Backup ZIP (includes DB + notification settings JSON)
- [x] Full activity log page with pagination
- [x] Unsaved changes guard on notifications page
- [x] Session timeout warning (2min toast before expiry, JS timer resets on activity)
- [x] Multi-interface support (MikroTik can have multiple WireGuard interfaces)
- [x] Live traffic sparklines per peer (Chart.js)
- [x] Expand row per peer (traffic chart + online/offline history)
- [x] Sort fix — peer rows and expand rows stay bonded when sorting wireguard table
- [x] IP blocked page (`blocked.html`) with `.env` instructions
- [x] Notification settings with unsaved-changes guard
- [x] LAN Access Mode per client — "Internet Only" (0.0.0.0/0), "LAN Only" (192.168.88.0/24), "Full Access" (both); stored as `access_mode` TEXT in clients table; three-button selector in create form and edit modal; badge in client list; shown in portal; clone inherits mode; regen and update_client respect/regenerate mode
- [x] PipSqueeze logo (`/static/logo.png`) — favicon + apple-touch-icon in all 10 templates; 80px centered above login h1; 32px in index.html topbar brand-row (replaces ⚡ emoji); og-image.png copy at `/static/og-image.png` for social preview
- [x] PWA manifest (`/static/manifest.json`) + service worker (`/static/sw.js`) — standalone display, cyan theme-color, apple-mobile-web-app meta tags in all templates; SW caches / + static assets; Flask route `/sw.js` serves with correct MIME type
- [x] Project folder renamed from `/var/www/vpn-dashboard` to `/var/www/pipsqueeze`; venv rebuilt (shebang paths); systemd service file updated; CLAUDE.md updated
- [x] Bandwidth quota per client — `quota_mb` column in clients table; quota amount + unit (MB/GB) in create form and edit modal; progress bar (green/yellow/red) in client list; auto-disable in monitor thread when quota exceeded; `notify_quota` notification toggle
- [x] Scheduled expiry reminders — monitor thread sends notification 3 days before client expires (once per day per client); `notify_expiry_reminder` toggle in notifications
- [x] Login failure notifications — sends alert on each failed login attempt with IP + attempt count; sends lockout alert when IP is locked out; `notify_login_failure` and `notify_login_locked` toggles
- [x] Auto-provision URLs — one-time links that create a WireGuard client on first visit (no login required); `provision_tokens` DB table; `/provision/manage` page; `provision.html` (shows config + QR), `provision_error.html`; inherits tags/access_mode/quota/expiry from token; `notify_provision` toggle; PROVISION button in topbar
- [x] Multi-user admin accounts — `admin_users` table with Werkzeug-hashed passwords (scrypt by default) + roles (admin/viewer); default admin seeded from .env on first run; login route uses DB; `admin_required` decorator for write routes; USERS button (admin only) in topbar; `/admin/users` management page; role badge in topbar
- [x] MikroTik firewall rule sync — address-list approach: one DROP rule (`pipsqueeze-lan-block`) blocks "internet" mode clients from LAN; `add_to_lan_block`/`remove_from_lan_block` called on create/delete/toggle/bulk/provision/access_mode change; `sync_firewall_rules()` reconciles on monitor thread first run; firewall rule count in `/api/mt-health` response
- [x] Automatic IP geolocation — `endpoint-address` captured from MikroTik peers in `get_peers()`; `geolocate_ip(ip)` calls **ipapi.co over HTTPS** (free, 1k/day, no key) with private-IP skip list; `_geo_cache` dict avoids repeat API calls; `_peer_endpoints` dict tracks latest endpoint per client; on peer connect (if no manual location) auto-sets lat/lon/location in DB; wireguard.html expand row shows endpoint IP + detected location; index.html shows 🌍 auto-location for clients without manual location; map_view() falls back to endpoint geo for clients with no DB lat/lon; map popup shows 📍 (manual) vs 🌍 (auto) with "(auto)" label
- [x] **CSRF protection** — Flask-WTF `CSRFProtect` initialized in app.py; `csrf_meta_tag()` injected into all admin templates; hidden `csrf_token` input on every server-rendered POST form (login, security, wireguard, admin_users, index, notifications, provision_manage, import, admin_api_keys); JS POSTs (notifications test via FormData picks up the field; report digest sends `X-CSRFToken` header from meta); custom CSRFError handler returns JSON for `/api/*` and a flash+redirect for forms; `WTF_CSRF_TIME_LIMIT=7200`, `WTF_CSRF_SSL_STRICT=False`
- [x] **Session cookie hardening** — `SESSION_COOKIE_SECURE=True` (env-overridable via `COOKIE_INSECURE=1` for local testing), `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE=Lax`
- [x] **At-rest encryption of notification credentials** — `vault.py` module wraps Fernet (AES-128-CBC + HMAC-SHA256) with `MultiFernet` for key rotation; sensitive fields (`discord_webhook`, `email_pass`, `telegram_token`) prefixed with `enc:` in DB; key sourcing: explicit `SECRET_VAULT_KEY` env (comma-separated for rotation) OR derived from `SECRET_KEY` via PBKDF2-HMAC-SHA256 (600k iters, salt `pipsqueeze-vault-v1`); `migrate_settings()` runs in `init_db()` to encrypt any pre-existing plaintext values
- [x] **Service worker versioning** — `CACHE_VERSION` constant in sw.js; cache name keyed off it; activate event purges old `pipsqueeze-*` caches; HTML pages now NEVER cached (would otherwise serve stale CSRF tokens), only `/static/*` falls back to cache when offline
- [x] **WireGuard import** — `/import` route lists MikroTik peers not yet tracked in DB (matched by name AND IP); `templates/import.html` lets admin pick peers + edit suggested name; POST `/import` registers selected peers into `clients` (without generating .conf since private key isn't ours), tags them `imported`, optionally syncs comment back to MikroTik; IMPORT button (admin only) in topbar
- [x] **API key access** — `api_keys` table (id, label, key_hash SHA-256, scope=read/write, created_at, last_used_at, revoked); `/admin/api-keys` page with create/revoke; one-time plaintext display via session flash; `@api_key_required(scope=...)` decorator accepts `Authorization: Bearer <key>` or `X-API-Key: <key>`; v1 endpoints under `/api/v1/`: `GET /clients`, `GET /peers`, `POST /clients/<name>/enable`, `POST /clients/<name>/disable`; all v1 routes `@csrf.exempt` (token auth)
- [x] **Auto-cleanup of never-connected clients** — `AUTO_CLEANUP_DAYS` env var (0/blank = disabled); monitor thread runs `_run_auto_cleanup()` once per UTC day; finds clients with `last_seen IS NULL AND created_at < (now - N days)`; deletes from MikroTik + lan-block address-list + .conf + QR + DB; sends a `delete` notification with the names removed
- [x] **Keyboard cheatsheet modal** — `?` key opens `#cheatModal` listing N/R/Esc/?; Esc closes; ignored while typing in inputs
- [x] **Range-selectable uptime chart** — `/api/uptime-history/<client>?days=N` accepts 1-90 days (clamped); wireguard expand row has 7d/30d/90d toggle buttons; chart x-axis ticks auto-skip for 30d/90d to stay readable
- [x] **Playwright + HTTP test suites** — `tests/` directory with `conftest.py` (spawns isolated test instance on port 5050 with copy of prod DB, deterministic admin + TOTP, COOKIE_INSECURE=1), `test_http_smoke.py` (15 tests, no browser), `test_ui_smoke.py` (21 tests, real Chromium); all 36 tests pass; tests cover every P0/P1/P2 change end-to-end
- [x] **2026-05-19 per-user TOTP secrets** — closes the long-standing "all admins share one 2FA seed" caveat. New `totp_secret` column on `admin_users` (auto-migrated). When set, the value is Fernet-encrypted via `vault.encrypt()` and the login route decrypts it for `pyotp.TOTP` verification. When NULL, the user falls back to the env `TOTP_SECRET` so existing users keep working until each one is migrated explicitly. New `RESET 2FA` action (admin only, CSRF-guarded, confirms before submitting) generates `pyotp.random_base32()`, stores encrypted, stashes plaintext in `session["pending_enrollment"]`, and redirects to the new `/admin/users/enroll/<uid>` page. That page renders a PNG QR (otpauth URI, `data:image/png;base64,…`), the textual secret, the raw URI, and a lock-out recovery hint (`sqlite3 vpn_dashboard.db "UPDATE admin_users SET totp_secret=NULL WHERE username=…"`). The session-pending stash is consumed on first GET so refresh redirects away. Admin Users page now shows a `PERSONAL` (green) vs `SHARED` (yellow) 2FA badge per row, surfacing the upgrade path. Two new HTTP tests verify the full round-trip (reset → enroll page renders → DB has `enc:` prefix → `vault.decrypt` returns the plaintext that was displayed → second GET of enroll redirects). All 38 tests pass.
- [x] **2026-05-19 seamless-transition UI layer** — new shared `/static/css/transitions.css` + `/static/js/transitions.js` injected into all 16 templates (after the `csrf-token` meta tag; `theme-color` anchor for `provision.html`). CSS enables Chrome/Safari MPA View Transitions via `@view-transition { navigation: auto; }` (no-ops on Firefox<128), adds modal scale+fade entry on `.modal-bg.open`, animated `.toast.show`/`.toast.hide` classes, top-of-page progress bar (`#pip-progress`), button active-press, `@media (prefers-reduced-motion: reduce)` killswitch. JS adds the progress-bar runtime (triggers on POST submits + same-origin link clicks), wraps any existing `window.showToast()` to drive the new animation classes, exposes `window.pipViewTransition(fn)` helper. Service worker `CACHE_VERSION` bumped to `2026-05-19a` so PWA users pick up the fresh assets. No `app.py` change needed — Flask serves `/static/*` directly. All 36 tests still pass.
- [x] **2026-05-10 security hardening pass** (post-audit) — `@admin_required` now enforced on every state-changing route (~25 routes: client CRUD, bulk-action, regen, rotate-portal, wireguard enable/disable, /notifications + save/test, /reset-db, /reset-all, /provision/*, /security/unlock, /security/clear-audit, /download, /qr, /backup, /api/send-digest); `home()` POST branch additionally checks role inline; viewer accounts are now genuinely read-only. Stored-XSS fixed in `templates/index.html` and `templates/admin_users.html` — replaced inline `onclick="openEdit('{{ ... }}', ...)"` with `data-*` attributes + delegated handlers; usernames now constrained to `[a-zA-Z0-9_.-]{1,32}` server-side. `werkzeug.middleware.proxy_fix.ProxyFix(x_for=1, x_proto=1, x_host=1)` wraps the WSGI app so X-Forwarded-For is only trusted from nginx (was previously spoofable by clients hitting gunicorn directly, breaking IP whitelist + rate-limit + audit log). New `@app.after_request` sets HSTS/X-Content-Type-Options/X-Frame-Options/Referrer-Policy/CSP (CSP keeps `'unsafe-inline'` for now since templates still ship inline styles/JS — tightening that is future work). SSRF guards: `validate_discord_webhook` (https only, host in {discord.com, discordapp.com, ptb.*, canary.*}, /api/webhooks/* path), `validate_smtp_host` (rejects hosts that resolve to private/loopback/link-local addresses), `validate_telegram_token` (no whitespace/URL chars). `notifications.html` no longer renders decrypted `discord_webhook` / `email_pass` / `telegram_token` back into the form — empty input means "keep stored value", filled means "replace"; route now passes `secret_set` flags so the template can show "configured (leave blank to keep)". Backup ZIP redacts plaintext credentials from `notification_settings.json`. CSV export defangs Excel formula injection (`=,+,-,@,\t,\r` cells prefixed with `'`). Login regenerates the session id (`session.clear()` before setting `logged_in`) to defeat fixation. CSRF error redirect validates same-origin Referer (was open redirect). Provision client-name suffix uses `secrets.token_hex(4)` (was `token_urlsafe(3)` — only ~18 bits). Removed `shell=True` from `wg genkey`/`wg pubkey` calls — new `_wg_keypair()` helper uses list args + `input=`. App now refuses to start if `SECRET_KEY`, `APP_USERNAME`, `APP_PASSWORD`, or `TOTP_SECRET` are missing/empty (closes the "default seeded admin uses 'changeme'" footgun). `app.run(debug=...)` gated on `FLASK_DEBUG=1` env var. Deleted stale `test_mt.py` from repo root. All 36 tests still pass.

---

## Bugs Fixed (chronological)

| Bug | Fix |
|-----|-----|
| access_mode column missing from existing DBs | Added to `init_db()` migration block — auto-migrates on startup |
| Delete client didn't remove peer from MikroTik | Added `mt.delete_peer_by_comment()` call before DB delete |
| Handshake parsing broke on `1h2m30s` format | Rewrote with regex for `(\d+)h`, `(\d+)m`, `(\d+)s` |
| Traffic totals inflated every 30s | Changed to delta tracking using `_prev_traffic` dict |
| Portal URL used fragile JS workaround | Now built server-side with `url_for()` and passed to template directly |
| Sort on wireguard page detached expand rows | Sort now builds (peer, expand) pairs and moves them together |
| Flash message re-fired on every page refresh | Added `sessionStorage` key check — fires only once per action |
| VSCode flagged Jinja in JS as errors | `data-key` attribute pattern for onclick; `html.validate.scripts: false` option |
| `psutil` missing on VPS after app.py update | `pip install psutil` inside venv |
| MikroTik "not enough permissions" on wireguard page | Router API user needed `read+write+api` policy group on MikroTik |
| `@anthropic/claude-code` 404 on npm | Correct package name is `@anthropic-ai/claude-code` |
| Create Client form missing from dashboard | `session.get('role') == 'admin'` returned False for stale pre-multi-user sessions; fixed by defaulting to 'admin' via `session.get('role', 'admin') == 'admin'` — users must re-login to get role in session |
| Map page split dark/white, tiles not dark | Replaced OSM + CSS filter with CartoDB Dark Matter tiles natively; removed `.leaflet-tile` filter rule |
| Map "No locations" overlay blocked map render | Removed full-page absolute overlay; replaced with small sidebar note; map now always renders regardless of client locations |
| Map server pin was hardcoded VPS location, not gateway | Replaced hardcoded VPS pin with MikroTik WireGuard gateway pin; location driven by `MT_LAT`/`MT_LON`/`MT_LOCATION_NAME` env vars; if blank, the gateway pin is omitted and the map shows a neutral world view; map route passes `mt_lat`/`mt_lon`/`mt_name`/`mt_iface` to template |
| endpoint-address not captured from MikroTik | Added endpoint_ip extraction in get_peers() (strips port from "1.2.3.4:51820" format) |
| Viewer accounts had full admin write access | `@admin_required` was defined but only attached to ~9 routes; ~25 mutating routes used `@login_required` only. Audited all routes and flipped the mutating ones to `@admin_required`. `home()` POST branch additionally re-checks role inline. |
| Stored XSS via inline `onclick="openEdit('{{ c.tags }}', ...)"` | Tags / location / access_mode / username with an apostrophe broke out of the JS string. Replaced inline handlers with `data-*` attributes + delegated `addEventListener("click", …)`; added server-side username validator. |
| `X-Forwarded-For` blindly trusted | Wrapped WSGI app in `ProxyFix(x_for=1, x_proto=1, x_host=1)` so only nginx's hop is trusted. |
| Notification page leaked decrypted Discord/SMTP/Telegram secrets back into form HTML | `notifications_page` now strips secret-shaped fields and passes `secret_set` flags; template renders placeholders. Save/test routes preserve stored value when input is empty. |
| Discord webhook / SMTP host could SSRF internal services | Added `validate_discord_webhook` (https + allowed hostnames + /api/webhooks path), `validate_smtp_host` (rejects private/loopback DNS resolutions), `validate_telegram_token`. Wired into `/notifications/save` and `/notifications/test`. |
| `wg genkey` / `wg pubkey` ran with `shell=True` | Replaced four call sites with a `_wg_keypair()` helper that uses list args and pipes via `input=`. |
| Default seeded admin used hashed `"changeme"` if `APP_PASSWORD` was unset | App now refuses to start when `APP_USERNAME`/`APP_PASSWORD`/`TOTP_SECRET`/`SECRET_KEY` are missing or empty. |
| CSRF error handler open-redirected via `Referer` | Now validates referer netloc matches `request.host`, falls back to `home`. |
| Login session id reused across pre-login → post-login | `session.clear()` before populating `logged_in` defeats fixation. |
| `app.run(debug=True)` was a footgun | Gated on `FLASK_DEBUG=1`. |
| Provision client-name suffix had ~18 bits of entropy (`token_urlsafe(3)` → 4 chars) | Switched to `secrets.token_hex(4)` (32 bits). |
| CSV export allowed Excel formula injection | Cells starting with `=,+,-,@,\t,\r` are now prefixed with a single quote. |
| Backup ZIP wrote decrypted Discord/SMTP/Telegram credentials into `notification_settings.json` | JSON now redacts the three secret fields; encrypted values still live in the DB row. |
| `/map` blank and peer charts empty — Leaflet/Chart.js from cdnjs blocked by CSP; CARTO tiles need an API key | cdnjs added to CSP; OSM tiles with referrerPolicy + attribution |
| Web fonts (and CDN libs) failed after the first visit | Service worker proxied cross-origin GETs under its own CSP; now ignores other origins |
| `/wireguard` 500 when MikroTik unreachable | try/except → banner; `/peers` 503 JSON |
| Dashboard never displayed flash messages | `base.html` renders flashes on every page |
| Monitor thread ran once per gunicorn worker (`-w 3`) — traffic totals, history rows and alerts multiplied | `_claim_monitor_lock()`: `fcntl.flock` on `.monitor.lock`, only the holder starts `_monitor_loop()`; replacement worker takes over if the owner dies |

---

## Design Decisions Made

| Decision | Reason |
|----------|--------|
| Tactical dark NOC aesthetic | Feels like real network infrastructure tooling, not generic web app |
| Rajdhani + Share Tech Mono font pairing | Sharp geometric display + monospace data — strong contrast |
| Electric cyan (#00c8ff) primary accent | Visible on dark, not as harsh as pure white, maps to "network/tech" |
| Neon green (#00ff9d) for online/healthy states | Universal "green = good" reinforced |
| No email for notifications initially | Added later — Telegram is simpler (no SMTP setup) |
| SQLite not Postgres | Single admin, low concurrency, zero ops overhead |
| Background thread not Celery | No Redis dependency, simpler deployment |
| Nominatim for geocoding | Free, no API key, works immediately |
| PAGE_SIZE = 20 | Reasonable default for client list pagination |

---

## Things NOT Done Yet (future work)

- [x] Mobile responsive overhaul (card layout for small screens)
- [x] Historical uptime chart — 7-day uptime bar chart in /wireguard expand-row; `/api/uptime-history/<client>` endpoint
- [x] Login attempt notifications (done — login_failure + login_locked toggles)
- [x] PWA support (manifest + service worker + meta tags)
- [x] Auto-provision URL
- [x] Bandwidth quota per client
- [x] Multi-user admin accounts
- [x] MikroTik firewall rule sync
- [x] Scheduled expiry reminders (3 days before)
- [x] Auto-cleanup (delete never-connected clients after X days) — `AUTO_CLEANUP_DAYS` env var
- [x] API key access for external scripts — `/api/v1/*` with `Authorization: Bearer` or `X-API-Key` headers
- [x] WireGuard config import (register existing peers created outside dashboard) — `/import` route
- [x] Historical uptime % chart — 7d/30d/90d toggle in /wireguard expand row
- [ ] Drag to reorder clients
- [x] Keyboard shortcut cheatsheet (`?` key) — opens `#cheatModal` from dashboard
- [x] SMTP password encryption at rest — Fernet via `vault.py`; encrypts discord_webhook, email_pass, telegram_token
- [x] Scheduled expiry reminders (already implemented, 3 days before)

---

## How to Update This File

After any significant session, add entries under the relevant sections:
- New features → add to "Features Implemented" checklist
- Bugs encountered and fixed → add to "Bugs Fixed" table
- Architectural choices → add to "Design Decisions Made" table
- Incomplete work → move from "Not Done Yet" or add new items
