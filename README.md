# WireGuard VPN Dashboard

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Flask](https://img.shields.io/badge/Flask-Web_App-black)
![WireGuard](https://img.shields.io/badge/VPN-WireGuard-green)
![MikroTik](https://img.shields.io/badge/Router-MikroTik-orange)
![Status](https://img.shields.io/badge/Status-Live-success)

A self-hosted WireGuard VPN management dashboard. Manages peers directly on a MikroTik router via the RouterOS API — no manual SSH or CLI needed. Built with Python/Flask, deployed on Ubuntu VPS via gunicorn + nginx.

**Live:** https://vpn.syedhashmi.trade *(login required)*

---

## Screenshots

### Login Page
<img width="550" height="736" alt="Login" src="https://github.com/user-attachments/assets/f4fbc194-6229-49e3-b89e-056aa9284c2d" />

### Dashboard
<img width="1234" height="973" alt="Dashboard" src="https://github.com/user-attachments/assets/edd6e56c-6ec1-4225-beac-a8c4384b3a93" />

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
        │                              │
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

- **Background thread** polls MikroTik every 30s — records traffic, ping, uptime, events
- **Notifications** via Discord webhook, SMTP email, or Telegram bot (`notifications.py`)
- **Client portal** at `/portal/<token>` — token-based config download, no login required

---

## Features

- Create WireGuard clients — auto-generates keys, config file, QR code
- Manage peers: enable/disable, rename, clone, delete, bulk actions
- LAN access modes per client: Internet Only / LAN Only / Full Access
- Client expiry with auto-disable
- Live peer status, traffic, ping, and uptime charts
- World map of client locations (Leaflet.js)
- 2FA login (TOTP), rate limiting, session timeout, IP whitelist
- Discord / Email / Telegram notifications with per-event toggles
- Weekly digest report
- Export to CSV, backup to ZIP

---

## Setup

### Prerequisites

- Ubuntu VPS with Python 3.12
- MikroTik router with WireGuard interface configured and API user enabled (`read+write+api` policy)
- nginx + systemd

### Install

```bash
git clone https://github.com/syedhashmi-bit/vpn-dashboard.git
cd vpn-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
```

### Run (development)

```bash
source venv/bin/activate
python app.py
```

### Deploy (production)

```bash
# systemd service: vpn-dashboard
systemctl restart vpn-dashboard
systemctl status vpn-dashboard
journalctl -u vpn-dashboard -n 50 --no-pager
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask, SQLite |
| Router API | RouterOS-api (MikroTik) |
| Frontend | Jinja2, Chart.js, Leaflet.js |
| Auth | pyotp (TOTP 2FA) |
| Server | Gunicorn, Nginx, Ubuntu |
