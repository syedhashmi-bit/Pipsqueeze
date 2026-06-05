#!/usr/bin/env bash
#
# PipSqueeze — guided installer
# Run after cloning the repo:
#
#   git clone https://github.com/syedhashmi-bit/Pipsqueeze.git
#   cd Pipsqueeze
#   ./install.sh
#
# The script walks you through every step interactively: prerequisites,
# virtualenv, secrets, .env, 2FA QR (in your terminal), and optionally
# the systemd service, nginx, and HTTPS via certbot.
#
# It is safe to re-run. Existing files (.env, the service) are never
# overwritten without asking first.

set -euo pipefail

# ── Paths & context ──────────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
VENV_PY="$PROJECT_DIR/venv/bin/python"
SERVICE_NAME="pipsqueeze"

if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi
INVOKING_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$USER}")"

# ── Pretty output ────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RESET=$'\e[0m'; C_BOLD=$'\e[1m'; C_DIM=$'\e[2m'
  C_CYAN=$'\e[38;5;45m'; C_GREEN=$'\e[38;5;48m'; C_RED=$'\e[38;5;203m'; C_YEL=$'\e[38;5;220m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_CYAN=""; C_GREEN=""; C_RED=""; C_YEL=""
fi

STEP=0
step()  { STEP=$((STEP+1)); printf '\n%s%s── Step %d ─ %s%s\n' "$C_BOLD" "$C_CYAN" "$STEP" "$1" "$C_RESET"; }
info()  { printf '   %s\n' "$1"; }
ok()    { printf '   %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$1"; }
warn()  { printf '   %s!%s %s\n' "$C_YEL" "$C_RESET" "$1"; }
die()   { printf '\n%s✗ %s%s\n' "$C_RED" "$1" "$C_RESET" >&2; exit 1; }

# ── Prompt helpers ───────────────────────────────────────────────────────────
# ask VAR "Prompt" "default"  → reads into VAR, falls back to default
ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __reply
  if [[ -n "$__default" ]]; then
    read -r -p "   $__prompt [$__default]: " __reply || true
    __reply="${__reply:-$__default}"
  else
    read -r -p "   $__prompt: " __reply || true
  fi
  printf -v "$__var" '%s' "$__reply"
}

# ask_required VAR "Prompt" "default" → loops until non-empty
ask_required() {
  while true; do
    ask "$1" "$2" "${3:-}"
    [[ -n "${!1}" ]] && break
    warn "This value is required."
  done
}

# ask_secret VAR "Prompt" → hidden input, loops until non-empty
ask_secret() {
  local __reply
  while true; do
    read -r -s -p "   $2: " __reply || true; echo
    [[ -n "$__reply" ]] && { printf -v "$1" '%s' "$__reply"; break; }
    warn "This value is required."
  done
}

# yesno "Question" "Y|N" → returns 0 for yes, 1 for no
yesno() {
  local __prompt="$1" __default="${2:-Y}" __reply __hint
  [[ "$__default" == "Y" ]] && __hint="[Y/n]" || __hint="[y/N]"
  read -r -p "   $__prompt $__hint: " __reply || true
  __reply="${__reply:-$__default}"
  [[ "$__reply" =~ ^[Yy] ]]
}

# ── Banner ───────────────────────────────────────────────────────────────────
cat <<BANNER
${C_BOLD}${C_CYAN}
  ╔═══════════════════════════════════════════╗
  ║            PipSqueeze installer           ║
  ║   self-hosted WireGuard VPN dashboard     ║
  ╚═══════════════════════════════════════════╝
${C_RESET}${C_DIM}  Project: $PROJECT_DIR${C_RESET}
BANNER

# ── Step 1 — prerequisites ───────────────────────────────────────────────────
step "Checking prerequisites"

command -v python3 >/dev/null 2>&1 || die "python3 not found. Install it first: $SUDO apt install python3"
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PYV_OK="$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3,10) else 0)')"
[[ "$PYV_OK" == "1" ]] || die "Python 3.10+ required (found $PYV)."
ok "Python $PYV"

# python3-venv is needed to create the virtualenv
if ! python3 -c 'import venv' 2>/dev/null; then
  warn "python3-venv module is missing."
  if yesno "Install python3-venv now (apt)?"; then
    $SUDO apt update && $SUDO apt install -y python3-venv
  else
    die "Cannot continue without python3-venv."
  fi
fi
ok "python3-venv available"

# Optional system packages used by later steps
MISSING_OPT=()
for pkg in nginx certbot wireguard-tools; do
  bin="$pkg"; [[ "$pkg" == "wireguard-tools" ]] && bin="wg"; [[ "$pkg" == "certbot" ]] && bin="certbot"
  command -v "$bin" >/dev/null 2>&1 || MISSING_OPT+=("$pkg")
done
if [[ ${#MISSING_OPT[@]} -gt 0 ]]; then
  warn "Optional packages not installed: ${MISSING_OPT[*]}"
  info "These are only needed for the reverse proxy / HTTPS steps."
  if yesno "Install them now (apt)?" "N"; then
    $SUDO apt update && $SUDO apt install -y "${MISSING_OPT[@]}"
    ok "Installed ${MISSING_OPT[*]}"
  else
    info "Skipping — you can install them later if you want nginx/HTTPS."
  fi
else
  ok "nginx, certbot, wireguard-tools present"
fi

# ── Step 2 — virtualenv + dependencies ───────────────────────────────────────
step "Setting up the Python virtual environment"

if [[ -x "$VENV_PY" ]]; then
  ok "venv already exists at $PROJECT_DIR/venv"
else
  info "Creating venv..."
  python3 -m venv venv
  ok "Created venv"
fi
info "Installing dependencies (this can take a minute)..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r requirements.txt
ok "Dependencies installed"

# ── Step 3 — configure .env ──────────────────────────────────────────────────
step "Configuring environment (.env)"

WRITE_ENV=1
if [[ -f .env ]]; then
  warn "An existing .env was found."
  if yesno "Keep it and skip configuration?" "Y"; then
    WRITE_ENV=0
    ok "Keeping existing .env"
  else
    cp .env ".env.backup.$(date +%s)"
    ok "Backed up existing .env"
  fi
fi

if [[ "$WRITE_ENV" == "1" ]]; then
  info "Generating secrets..."
  SECRET_KEY="$("$VENV_PY" -c 'import secrets; print(secrets.token_hex(32))')"
  TOTP_SECRET="$("$VENV_PY" -c 'import pyotp; print(pyotp.random_base32())')"
  ok "Generated SECRET_KEY and TOTP_SECRET"

  echo
  info "${C_BOLD}Admin login${C_RESET}"
  ask_required APP_USERNAME "Admin username" "admin"
  ask_secret   APP_PASSWORD "Admin password (hidden)"

  echo
  info "${C_BOLD}WireGuard server${C_RESET} (written into every client .conf)"
  ask_required SERVER_PUBLIC_KEY "MikroTik WireGuard interface PUBLIC key"
  ask_required SERVER_IP         "VPS public IP or domain (client Endpoint)"
  ask          SERVER_PORT       "WireGuard listen port" "51820"
  ask          CLIENT_DNS        "DNS pushed to clients" "1.1.1.1"

  echo
  info "${C_BOLD}MikroTik RouterOS API${C_RESET}"
  ask_required MT_HOST     "MikroTik router LAN IP"
  ask          MT_USERNAME "MikroTik API username" "api"
  ask_secret   MT_PASSWORD "MikroTik API password (hidden)"
  ask          MT_PORT     "MikroTik API port (8728 plain / 8729 TLS)" "8728"
  ask          MT_WIREGUARD_INTERFACE "WireGuard interface name on MikroTik" "WireGuard1"

  umask 077
  cat > .env <<ENV
# Generated by install.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Advanced/optional settings are documented in .env.example

# ── App ──
SECRET_KEY=$SECRET_KEY
APP_USERNAME=$APP_USERNAME
APP_PASSWORD=$APP_PASSWORD
TOTP_SECRET=$TOTP_SECRET

# ── WireGuard server ──
SERVER_PUBLIC_KEY=$SERVER_PUBLIC_KEY
SERVER_IP=$SERVER_IP
SERVER_PORT=$SERVER_PORT
CLIENT_DNS=$CLIENT_DNS

# ── MikroTik RouterOS API ──
MT_HOST=$MT_HOST
MT_USERNAME=$MT_USERNAME
MT_PASSWORD=$MT_PASSWORD
MT_PORT=$MT_PORT
MT_WIREGUARD_INTERFACE=$MT_WIREGUARD_INTERFACE

# ── Security (optional — defaults shown) ──
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=15
SESSION_TIMEOUT_MIN=30
IP_WHITELIST=

# ── Notifications / cleanup (optional) ──
WEEKLY_DIGEST_DAY=monday
AUTO_CLEANUP_DAYS=
ENV
  umask 022
  chmod 600 .env
  ok "Wrote .env (permissions 600)"
fi

# ── Step 4 — two-factor authentication ───────────────────────────────────────
step "Two-factor authentication (2FA)"
info "Scan this QR with Google Authenticator, Authy, or 1Password."
info "If you kept an existing .env, this matches its TOTP_SECRET."
echo
"$VENV_PY" - <<'PY'
import os, pyotp
from dotenv import load_dotenv
load_dotenv()
secret = os.getenv("TOTP_SECRET")
user   = os.getenv("APP_USERNAME", "admin")
if not secret:
    print("   (no TOTP_SECRET in .env — skipping QR)")
else:
    import qrcode
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=user, issuer_name="PipSqueeze")
    qr = qrcode.QRCode(border=2)
    qr.add_data(uri); qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("   Secret (manual entry):", secret)
PY
echo
read -r -p "   Press Enter once you've added the 2FA code to your app... " _ || true

# ── Step 5 — systemd service ─────────────────────────────────────────────────
step "System service (systemd)"
if ! command -v systemctl >/dev/null 2>&1; then
  warn "systemd not detected — skipping. Run the app with: $VENV_PY app.py"
elif yesno "Create and start the '$SERVICE_NAME' systemd service?"; then
  ask SERVICE_USER "Run the service as which user?" "$INVOKING_USER"
  UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
  $SUDO tee "$UNIT" >/dev/null <<UNITFILE
[Unit]
Description=PipSqueeze VPN Dashboard
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=5
Environment=PATH=$PROJECT_DIR/venv/bin

[Install]
WantedBy=multi-user.target
UNITFILE
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
  $SUDO systemctl restart "$SERVICE_NAME"
  sleep 2
  if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service '$SERVICE_NAME' is running on 127.0.0.1:5000"
  else
    warn "Service did not start cleanly. Check: journalctl -u $SERVICE_NAME -n 50 --no-pager"
  fi
else
  info "Skipped. Start manually with: $VENV_PY app.py"
fi

# ── Step 6 — nginx reverse proxy ─────────────────────────────────────────────
step "Reverse proxy (nginx)"
if ! command -v nginx >/dev/null 2>&1; then
  info "nginx not installed — skipping. App is reachable on 127.0.0.1:5000."
elif yesno "Configure an nginx site for PipSqueeze?" "N"; then
  ask_required DOMAIN "Domain name (A record must point to this VPS)"
  SITE="/etc/nginx/sites-available/${SERVICE_NAME}"
  $SUDO tee "$SITE" >/dev/null <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }

    location /static/ {
        alias   $PROJECT_DIR/static/;
        expires 7d;
    }

    client_max_body_size 10M;
}
NGINX
  $SUDO ln -sf "$SITE" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
  if $SUDO nginx -t; then
    $SUDO systemctl reload nginx
    ok "nginx configured for $DOMAIN"

    # ── Step 7 — HTTPS via certbot ──
    if command -v certbot >/dev/null 2>&1 && yesno "Obtain a free HTTPS certificate now (certbot)?"; then
      $SUDO certbot --nginx -d "$DOMAIN" && ok "HTTPS enabled for https://$DOMAIN" \
        || warn "certbot failed — re-run later with: $SUDO certbot --nginx -d $DOMAIN"
    else
      warn "No HTTPS yet. PipSqueeze sets Secure cookies and will not log you in over plain HTTP."
      info "Run later: $SUDO certbot --nginx -d $DOMAIN   (or set COOKIE_INSECURE=1 in .env for local testing only)"
    fi
  else
    warn "nginx config test failed — not reloading. Fix the config and run: $SUDO nginx -t"
  fi
else
  info "Skipped nginx."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
DASH_URL="http://127.0.0.1:5000"
[[ -n "${DOMAIN:-}" ]] && DASH_URL="https://$DOMAIN"
printf '\n%s%s── Done ──%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
ok "PipSqueeze is installed."
info "Dashboard: ${C_BOLD}$DASH_URL${C_RESET}"
info "Login:     username from APP_USERNAME + your password + the 6-digit 2FA code"
echo
info "Useful commands:"
info "  $SUDO systemctl status $SERVICE_NAME"
info "  $SUDO journalctl -u $SERVICE_NAME -f"
info "  edit .env then: $SUDO systemctl restart $SERVICE_NAME"
echo
