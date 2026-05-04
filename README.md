<p align="center">
  <img width="120" height="120" alt="PipSqueeze" src="https://github.com/user-attachments/assets/b790ee14-16e8-432c-982b-ec50f4f67905" />
</p>

<h1 align="center">PipSqueeze</h1>

<p align="center">
  Self-hosted WireGuard VPN dashboard for MikroTik routers — like Tailscale, but yours.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-3.x-black?logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/WireGuard-VPN-green?logo=wireguard&logoColor=white" />
  <img src="https://img.shields.io/badge/MikroTik-RouterOS_API-orange" />
  <img src="https://img.shields.io/badge/License-MIT-lightgrey" />
</p>

---

## What is PipSqueeze?

PipSqueeze is a self-hosted WireGuard VPN management dashboard that talks directly to your MikroTik router via the RouterOS API. No manual SSH, no CLI commands — create clients, download configs, and monitor your VPN from any browser.

**It's like Tailscale, but entirely yours:**

- Create WireGuard clients and get a `.conf` file + QR code instantly
- Manage peers: enable/disable, rename, clone, bulk actions, expiry dates
- Three access modes per client: **Internet Only / LAN Only / Full Access**
- Live peer status with traffic sparklines, ping latency, and 7-day uptime %
- 2FA login (TOTP), rate limiting, session timeout, IP whitelist
- Discord / Email / Telegram notifications with per-event toggles
- Self-serve client portal — clients download their own config via a unique link (no login)
- World map of client locations (Leaflet.js + OpenStreetMap)
- Weekly usage digest, CSV export, full backup ZIP

---

## Screenshots

### Login Page
<img width="1012" height="1035" alt="Login" src="https://github.com/user-attachments/assets/fc31140b-f2b5-4ba0-a3b9-ddc08f1469eb" />

### Dashboard
<img width="962" height="1250" alt="Dashboard" src="https://github.com/user-attachments/assets/d773d671-a36c-4a26-b170-c232fe97b84f" />

### WireGuard Peers
<img width="996" height="321" alt="Peers" src="https://github.com/user-attachments/assets/af27bbb9-36d3-4c60-b52c-390234521752" />

### QR Code Generation
<img width="711" height="1023" alt="QR Code" src="https://github.com/user-attachments/assets/ed99bd4d-655c-4af3-bd9a-a02ad7c78b57" />

---

## Architecture

```
        ┌──────────────────────────────┐
        │         User Browser         │
        └──────────────┬───────────────┘
                       │ HTTPS
                       ▼
        ┌──────────────────────────────┐
        │     Nginx (reverse proxy)    │
        └──────────────┬───────────────┘
                       ▼
        ┌──────────────────────────────┐
        │   Gunicorn + Flask (app.py)  │
        │  Routes / Auth / Monitor     │
        └───────┬──────────────┬───────┘
                │              │
                ▼              ▼
     ┌─────────────────┐  ┌───────────────────┐
     │  SQLite DB      │  │  MikroTik Router   │
     │ vpn_dashboard   │  │  RouterOS API      │
     │     .db         │  │  (mikrotik_api.py) │
     └─────────────────┘  └───────────────────┘
```

A background thread polls MikroTik every 30 seconds — recording traffic deltas, ping latency, uptime status, and connect/disconnect events.

---

## Prerequisites

Before you begin, make sure you have:

- A **MikroTik router** (running RouterOS 7.x) with a WireGuard interface already configured
- A **VPS or server** running Ubuntu 20.04 or later (2 GB RAM minimum recommended)
- A **domain name** with an A record pointing to your VPS IP
- **Python 3.10+** on the server (`python3 --version` to check)
- **nginx** — `apt install nginx`
- **Certbot** for free HTTPS — `apt install certbot python3-certbot-nginx`
- **WireGuard tools** on the server — `apt install wireguard-tools`

---

## Installation

### Step 1 — Clone the repository

```bash
git clone https://github.com/syedhashmi-bit/pipsqueeze.git /var/www/pipsqueeze
cd /var/www/pipsqueeze
```

### Step 2 — Create the virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Configure your environment

```bash
cp .env.example .env
nano .env
```

Fill in every variable. The most critical ones to set before first boot:

| Variable | What to put here |
|----------|-----------------|
| `SECRET_KEY` | A long random string — run `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `APP_USERNAME` | Your admin login username |
| `APP_PASSWORD` | A strong admin password |
| `TOTP_SECRET` | Generate in Step 8 and come back |
| `SERVER_PUBLIC_KEY` | Public key of your WireGuard interface on MikroTik |
| `SERVER_IP` | Your VPS public IP or domain (written into every client `.conf` as the endpoint) |
| `SERVER_PORT` | WireGuard listen port on MikroTik (usually `51820`) |
| `CLIENT_DNS` | DNS pushed to VPN clients (e.g. `1.1.1.1` or your router IP) |
| `MT_HOST` | Your MikroTik router's LAN IP address |
| `MT_USERNAME` | MikroTik API user (create one in Step 4) |
| `MT_PASSWORD` | MikroTik API user password |
| `MT_WIREGUARD_INTERFACE` | Exact name of the WireGuard interface on MikroTik (e.g. `WireGuard1`) |

See the full [Configuration Reference](#configuration-reference) table for all options.

### Step 4 — Set up MikroTik

On your MikroTik router, do the following. You can use Winbox, WebFig, or the terminal.

**1. Create a WireGuard interface** (if you haven't already):

- Winbox → **WireGuard** → click `+`
- Give it a name (e.g. `WireGuard1`)
- Set the **Listen Port** to `51820`
- Click **OK** — RouterOS generates the key pair automatically
- Open the interface you just created and copy the **Public Key** — you'll need it for `SERVER_PUBLIC_KEY`

**2. Create an API user**:

- Winbox → **System → Users** → click the **Groups** tab → click `+`
- Name the group (e.g. `api-group`)
- Under **Policies**, check: `read`, `write`, `api`
- Click **OK**
- Now go to the **Users** tab → click `+`
- Set a username (e.g. `api`) and a strong password
- Set **Group** to the group you just created
- Click **OK**

**3. Enable the API service**:

- Winbox → **IP → Services**
- Find `api` in the list — make sure it is **enabled** and listening on port `8728`

**4. Note down**: interface name, MikroTik LAN IP, API username, API password.

### Step 5 — Create the systemd service

Create the file `/etc/systemd/system/pipsqueeze.service`:

```bash
nano /etc/systemd/system/pipsqueeze.service
```

Paste the following (adjust `User` if your VPS user is not `root`):

```ini
[Unit]
Description=PipSqueeze VPN Dashboard
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/pipsqueeze
ExecStart=/var/www/pipsqueeze/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=5
Environment=PATH=/var/www/pipsqueeze/venv/bin

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
systemctl daemon-reload
systemctl enable pipsqueeze
systemctl start pipsqueeze
systemctl status pipsqueeze
```

The app is now running on `127.0.0.1:5000`. nginx will proxy to it next.

### Step 6 — Set up nginx

Create a new site config:

```bash
nano /etc/nginx/sites-available/pipsqueeze
```

Paste the block below, replacing `YOUR_DOMAIN` with your actual domain:

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location /static/ {
        alias   /var/www/pipsqueeze/static/;
        expires 7d;
    }

    client_max_body_size 10M;
}
```

Enable the site and reload nginx:

```bash
ln -s /etc/nginx/sites-available/pipsqueeze /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### Step 7 — Point your domain and get HTTPS

1. In your DNS provider, add an **A record**: `YOUR_DOMAIN` → your VPS IP address
2. Wait a few minutes for DNS to propagate, then run Certbot:

```bash
certbot --nginx -d YOUR_DOMAIN
```

Certbot will automatically update your nginx config and configure auto-renewal. Your dashboard is now accessible at `https://YOUR_DOMAIN`.

Verify auto-renewal is scheduled:

```bash
systemctl status certbot.timer
```

### Step 8 — Set up two-factor authentication (2FA)

PipSqueeze requires TOTP 2FA on every login (same standard as Google Authenticator and Authy).

**Generate a TOTP secret:**

```bash
source /var/www/pipsqueeze/venv/bin/activate
python3 -c "import pyotp; print(pyotp.random_base32())"
```

Copy the output (it looks like `JBSWY3DPEHPK3PXP`) and add it to your `.env`:

```
TOTP_SECRET=JBSWY3DPEHPK3PXP
```

**Get a QR code to scan into your authenticator app:**

```bash
python3 -c "
import pyotp, os
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv('TOTP_SECRET')
uri = pyotp.totp.TOTP(secret).provisioning_uri(name='admin', issuer_name='PipSqueeze')
print(uri)
"
```

Paste the `otpauth://` URI into any online QR generator and scan it with **Google Authenticator**, **Authy**, or **1Password**. Alternatively, enter the raw secret manually in your app.

Restart the service to pick up the new `.env` value:

```bash
systemctl restart pipsqueeze
```

### Step 9 — First login

Visit `https://YOUR_DOMAIN` in your browser.

- **Username**: the value you set for `APP_USERNAME`
- **Password**: the value you set for `APP_PASSWORD`
- **2FA code**: the current 6-digit code from your authenticator app

You're in. Create your first WireGuard client from the dashboard.

---

## Configuration Reference

All configuration lives in `.env`. Copy `.env.example` to get started.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **Yes** | — | Flask session encryption key. Use a 32+ char random hex string. |
| `APP_USERNAME` | **Yes** | — | Admin login username |
| `APP_PASSWORD` | **Yes** | — | Admin login password |
| `TOTP_SECRET` | **Yes** | — | Base32 TOTP secret for 2FA (generate with `pyotp.random_base32()`) |
| `SERVER_PUBLIC_KEY` | **Yes** | — | WireGuard public key of your MikroTik interface — written into every client `.conf` |
| `SERVER_IP` | **Yes** | — | Your VPS public IP or domain — the `Endpoint` in client configs |
| `SERVER_PORT` | **Yes** | `51820` | WireGuard UDP listen port on MikroTik |
| `CLIENT_DNS` | **Yes** | — | DNS server written into client configs (e.g. `1.1.1.1`) |
| `MT_HOST` | **Yes** | — | MikroTik router LAN IP address |
| `MT_USERNAME` | **Yes** | — | MikroTik API username |
| `MT_PASSWORD` | **Yes** | — | MikroTik API password |
| `MT_PORT` | No | `8728` | MikroTik API port (`8728` = plaintext, `8729` = TLS) |
| `MT_WIREGUARD_INTERFACE` | **Yes** | — | Exact WireGuard interface name on MikroTik (e.g. `WireGuard1`) |
| `MAX_LOGIN_ATTEMPTS` | No | `5` | Failed logins before IP lockout |
| `LOCKOUT_MINUTES` | No | `15` | Lockout duration in minutes |
| `SESSION_TIMEOUT_MIN` | No | `30` | Inactivity timeout before auto-logout |
| `IP_WHITELIST` | No | *(allow all)* | Comma-separated IPs allowed to access the dashboard. Blank = no restriction. |
| `WEEKLY_DIGEST_DAY` | No | `monday` | Day of the week to send the weekly digest email |

---

## Updating

```bash
cd /var/www/pipsqueeze
git pull
source venv/bin/activate
pip install -r requirements.txt
systemctl restart pipsqueeze
```

---

## Troubleshooting

**Service won't start**

```bash
journalctl -u pipsqueeze -n 50 --no-pager
```

Look for missing packages (`pip install -r requirements.txt`) or bad `.env` values (missing required keys, syntax errors).

---

**"Not enough permissions" from MikroTik**

Your API user's group policy must include `read`, `write`, and `api`. Check in Winbox → System → Users → Groups.

---

**2FA code rejected**

TOTP requires your server clock to be within ~30 seconds of real time.

```bash
timedatectl status
```

If the clock is off, sync it:

```bash
timedatectl set-ntp true
```

---

**Can't reach the dashboard after install**

Check each layer in order:

```bash
systemctl status pipsqueeze        # app running?
systemctl status nginx             # nginx running?
nginx -t                           # nginx config valid?
dig YOUR_DOMAIN                    # DNS propagated?
ufw allow 'Nginx Full'             # firewall open on 80/443?
```

---

**MikroTik API connection refused**

- Confirm the API service is enabled: Winbox → IP → Services → `api` should show **enabled**
- Confirm `MT_HOST` is reachable from your VPS: `ping <MT_HOST>`
- If MikroTik is behind NAT, ensure port `8728` is port-forwarded to the router

---

**How to view live logs**

```bash
journalctl -u pipsqueeze -f
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask, SQLite |
| Router API | RouterOS-api (MikroTik) |
| Frontend | Jinja2, Chart.js sparklines, Leaflet.js map |
| Auth | pyotp (TOTP 2FA), rate limiting, session timeout |
| Server | Gunicorn, Nginx, Ubuntu |
| Notifications | Discord Webhooks, SMTP Email, Telegram Bot API |

---

## License

MIT — use it, modify it, self-host it.
