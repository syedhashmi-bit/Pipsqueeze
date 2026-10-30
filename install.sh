#!/usr/bin/env bash
#
# PipSqueeze — guided installer
# Run after cloning the repo:
#
#   git clone https://github.com/syedhashmi-bit/Pipsqueeze.git
#   cd Pipsqueeze
#   ./install.sh
#
# It starts by asking where PipSqueeze will run, then walks you through the
# steps for that setup:
#
#   1) Public server with a domain  → nginx + HTTPS (certbot), Google sign-in optional
#   2) Home/office network, no domain → http://<LAN IP>:<port>, password + 2FA
#   3) This machine only             → http://127.0.0.1:<port>, password + 2FA
#
# Every setup gets: prerequisites, virtualenv, secrets, .env, the 2FA QR (in
# your terminal) and the systemd service.
#
# It is safe to re-run. Existing files (.env, the service, the nginx site) are
# never overwritten without asking first.

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
# If input runs out (e.g. the script was piped into bash) stop, rather than
# looping forever on a required answer that can never arrive.
no_input() { die "Input ended — run ./install.sh from an interactive terminal."; }

# ask VAR "Prompt" "default"  → reads into VAR, falls back to default
ask() {
  local __var="$1" __prompt="$2" __default="${3:-}" __reply=""
  if [[ -n "$__default" ]]; then
    read -r -p "   $__prompt [$__default]: " __reply || [[ -n "$__reply" ]] || no_input
    __reply="${__reply:-$__default}"
  else
    read -r -p "   $__prompt: " __reply || [[ -n "$__reply" ]] || no_input
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
  local __reply=""
  while true; do
    read -r -s -p "   $2: " __reply || [[ -n "$__reply" ]] || no_input; echo
    [[ -n "$__reply" ]] && { printf -v "$1" '%s' "$__reply"; break; }
    warn "This value is required."
  done
}

# yesno "Question" "Y|N" → returns 0 for yes, 1 for no
yesno() {
  local __prompt="$1" __default="${2:-Y}" __reply="" __hint
  [[ "$__default" == "Y" ]] && __hint="[Y/n]" || __hint="[y/N]"
  read -r -p "   $__prompt $__hint: " __reply || [[ -n "$__reply" ]] || no_input
  __reply="${__reply:-$__default}"
  [[ "$__reply" =~ ^[Yy] ]]
}

# ask_port "Prompt" → PORT, a free (or knowingly reused) TCP port
ask_port() {
  while true; do
    ask PORT "$1" "5000"
    if ! [[ "$PORT" =~ ^[0-9]{1,5}$ ]] || (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
      warn "Enter a port number from 1 to 65535."
      continue
    fi
    PORT=$((10#$PORT))
    if command -v ss >/dev/null 2>&1 && ss -Hltn "sport = :$PORT" 2>/dev/null | grep -q .; then
      warn "Something is already listening on port $PORT (fine if it's PipSqueeze from an earlier run)."
      yesno "Use port $PORT anyway?" "N" || continue
    fi
    break
  done
}

# google_on → .env has everything Sign in with Google needs (mirrors google_auth.is_configured)
google_on() {
  grep -Eq '^GOOGLE_CLIENT_ID=.+' .env && grep -Eq '^GOOGLE_CLIENT_SECRET=.+' .env \
    && grep -Eq '^GOOGLE_(ADMIN|VIEWER)_EMAILS=.+' .env
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

# ── Where it runs ────────────────────────────────────────────────────────────
step "Where will PipSqueeze run?"
info "1) Public server with a domain — open it from anywhere over HTTPS."
info "   Sets up nginx and a free Let's Encrypt certificate. Google sign-in available."
info "2) Home or office network, no domain — open it from devices on your network"
info "   at http://<this machine's IP>:<port>. No domain or certificate needed."
info "3) This machine only — http://127.0.0.1:<port>, e.g. to try it out."
echo
while true; do
  ask MODE_PICK "Choose 1, 2 or 3" "1"
  case "$MODE_PICK" in
    1) MODE="public"; break ;;
    2) MODE="lan";    break ;;
    3) MODE="local";  break ;;
    *) warn "Enter 1, 2 or 3." ;;
  esac
done

case "$MODE" in
  public)
    while true; do
      ask_required DOMAIN "Domain name for the dashboard (its DNS A record must point to this server)"
      DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
      if [[ "$DOMAIN" =~ ^[0-9.]+$ || "$DOMAIN" == *:* ]]; then
        warn "That's an IP address. HTTPS certificates and Google sign-in need a domain name."
        info "No domain? Re-run and pick option 2."
        continue
      fi
      break
    done
    ask_port "Internal port (nginx forwards to it; it isn't opened to the internet)"
    BIND="127.0.0.1:$PORT"
    ok "Public server: https://$DOMAIN"
    ;;
  lan)
    LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i+1); exit}}' || true)"
    [[ -n "$LAN_IP" ]] || LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    [[ -n "$LAN_IP" ]] || LAN_IP="<this machine's IP>"
    ask_port "Port to open the dashboard on"
    BIND="0.0.0.0:$PORT"
    ok "Home/office network: http://$LAN_IP:$PORT"
    ;;
  local)
    ask_port "Port to open the dashboard on"
    BIND="127.0.0.1:$PORT"
    ok "This machine only: http://127.0.0.1:$PORT"
    ;;
esac

# ── Prerequisites ────────────────────────────────────────────────────────────
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

# wg generates every client's key pair. nginx + certbot are only for a public server.
NEED_PKGS=()
command -v wg >/dev/null 2>&1 || NEED_PKGS+=(wireguard-tools)
if [[ "$MODE" == "public" ]]; then
  command -v nginx >/dev/null 2>&1 || NEED_PKGS+=(nginx)
  if ! command -v certbot >/dev/null 2>&1; then
    NEED_PKGS+=(certbot python3-certbot-nginx)
  elif dpkg -s certbot >/dev/null 2>&1 && ! dpkg -s python3-certbot-nginx >/dev/null 2>&1; then
    NEED_PKGS+=(python3-certbot-nginx)   # apt's certbot needs it for --nginx; the snap has it built in
  fi
fi
if [[ ${#NEED_PKGS[@]} -gt 0 ]]; then
  warn "Missing packages: ${NEED_PKGS[*]}"
  if yesno "Install them now (apt)?"; then
    $SUDO apt update && $SUDO apt install -y "${NEED_PKGS[@]}"
    ok "Installed ${NEED_PKGS[*]}"
  else
    if ! command -v wg >/dev/null 2>&1; then
      warn "Without wireguard-tools PipSqueeze can't generate client keys, so creating clients will fail."
    fi
    if [[ "$MODE" == "public" ]]; then
      warn "Without nginx and certbot, https://$DOMAIN won't reach the dashboard."
    fi
  fi
else
  if [[ "$MODE" == "public" ]]; then ok "wireguard-tools, nginx and certbot present"; else ok "wireguard-tools present"; fi
fi

# ── Virtualenv + dependencies ────────────────────────────────────────────────
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

# ── Configure .env ───────────────────────────────────────────────────────────
step "Configuring environment (.env)"

WRITE_ENV=1
if [[ -f .env ]]; then
  warn "An existing .env was found."
  if yesno "Keep it and skip configuration?" "Y"; then
    WRITE_ENV=0
    ok "Keeping existing .env"
    if [[ "$MODE" != "public" ]] && ! grep -q '^COOKIE_INSECURE=1' .env; then
      warn "This setup serves the dashboard over plain http, and your .env doesn't set COOKIE_INSECURE=1,"
      warn "so the browser would drop the login cookie and you'd never get past the login page."
      if yesno "Add COOKIE_INSECURE=1 to .env?"; then
        printf '\n# Plain-http install (no domain / HTTPS): lets the login cookie work over http://\nCOOKIE_INSECURE=1\n' >> .env
        ok "Added COOKIE_INSECURE=1"
      fi
    elif [[ "$MODE" == "public" ]] && grep -q '^COOKIE_INSECURE=1' .env; then
      warn "Your .env sets COOKIE_INSECURE=1. Remove it once HTTPS works, so cookies are HTTPS-only."
    fi
    if [[ "$MODE" != "public" ]] && google_on; then
      warn "Your .env turns on Google sign-in, which only works on a public domain with HTTPS."
      warn "While it's on, it's the only login. Clear GOOGLE_CLIENT_ID in .env to log in with a password."
    fi
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
  info "${C_BOLD}WireGuard server${C_RESET} (written into every client .conf)"
  ask_required SERVER_PUBLIC_KEY "MikroTik WireGuard interface PUBLIC key"
  ask_required SERVER_IP         "Public IP or hostname VPN clients connect to (WireGuard Endpoint)"
  ask          SERVER_PORT       "WireGuard listen port" "51820"
  ask          CLIENT_DNS        "DNS pushed to clients" "1.1.1.1"

  echo
  info "${C_BOLD}MikroTik RouterOS API${C_RESET}"
  ask_required MT_HOST     "MikroTik router LAN IP"
  ask          MT_USERNAME "MikroTik API username" "api"
  ask_secret   MT_PASSWORD "MikroTik API password (hidden)"
  ask          MT_PORT     "MikroTik API port (8728 plain / 8729 TLS)" "8728"
  ask          MT_WIREGUARD_INTERFACE "WireGuard interface name on MikroTik" "WireGuard1"

  echo
  info "${C_BOLD}Sign-in${C_RESET}"
  GOOGLE_CLIENT_ID="" GOOGLE_CLIENT_SECRET="" GOOGLE_ADMIN_EMAILS="" GOOGLE_VIEWER_EMAILS=""
  if [[ "$MODE" == "public" ]]; then
    info "Sign in with Google lets a list of Google accounts in, instead of a password + 2FA code."
    if yesno "Use Sign in with Google?" "N"; then
      info "In Google Cloud Console → APIs & Services → Credentials, create an OAuth client"
      info "(type: Web application) with this authorized redirect URI:"
      info "  ${C_BOLD}https://$DOMAIN/auth/google/callback${C_RESET}"
      ask_required GOOGLE_CLIENT_ID     "Google OAuth client ID"
      ask_secret   GOOGLE_CLIENT_SECRET "Google OAuth client secret (hidden)"
      ask_required GOOGLE_ADMIN_EMAILS  "Google emails allowed as admin (comma-separated)"
      ask          GOOGLE_VIEWER_EMAILS "Google emails allowed as viewer (optional)" ""
      echo
      info "Fallback admin login — only used if you turn Google sign-in off later:"
    fi
  else
    info "Google sign-in needs a public domain with HTTPS, so this setup logs in"
    info "with a username, password and 2FA code."
  fi
  ask_required APP_USERNAME "Admin username" "admin"
  ask_secret   APP_PASSWORD "Admin password (hidden)"

  HTTP_BLOCK=""
  if [[ "$MODE" != "public" ]]; then
    HTTP_BLOCK=$'\n# ── Plain-http install (no domain / HTTPS) ──\n# Lets the login cookie work over http://. Remove it if you put HTTPS in front later.\nCOOKIE_INSECURE=1\n'
  fi

  umask 077
  cat > .env <<ENV
# Generated by install.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ") (setup: $MODE)
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

# ── Google sign-in (optional — blank = password + 2FA only) ──
GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET
GOOGLE_ADMIN_EMAILS=$GOOGLE_ADMIN_EMAILS
GOOGLE_VIEWER_EMAILS=$GOOGLE_VIEWER_EMAILS
${HTTP_BLOCK}
# ── Notifications / cleanup (optional) ──
WEEKLY_DIGEST_DAY=monday
AUTO_CLEANUP_DAYS=
ENV
  umask 022
  chmod 600 .env
  ok "Wrote .env (permissions 600)"
fi

# ── Two-factor authentication ────────────────────────────────────────────────
step "Two-factor authentication (2FA)"
if google_on; then
  info "Google sign-in is on, so this code is only for the fallback password login"
  info "(if you ever turn Google off). Scan it anyway and keep it somewhere safe."
else
  info "Scan this QR with Google Authenticator, Authy, or 1Password."
fi
info "If you kept an existing .env, this matches its TOTP_SECRET."
echo
# load_dotenv() with no path can't find .env from a stdin script (python-dotenv
# walks the caller's frames and asserts), so name the file.
"$VENV_PY" - <<'PY'
import os, pyotp
from dotenv import load_dotenv
load_dotenv(".env")
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

# ── systemd service ──────────────────────────────────────────────────────────
step "System service (systemd)"
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
GUNICORN_CMD="$PROJECT_DIR/venv/bin/gunicorn -w 1 -b $BIND app:app"
SERVICE_Q="Create and start the '$SERVICE_NAME' systemd service?"
if [[ -f "$UNIT" ]]; then
  SERVICE_Q="A '$SERVICE_NAME' service already exists. Replace it (listening on $BIND) and restart it?"
fi
if ! command -v systemctl >/dev/null 2>&1; then
  warn "systemd not detected — skipping. Start the app with: $GUNICORN_CMD"
elif yesno "$SERVICE_Q"; then
  ask SERVICE_USER "Run the service as which user?" "$INVOKING_USER"
  # Ports below 1024 are root-only; let a non-root service user bind them.
  LOW_PORT_CAP=""
  if (( PORT < 1024 )); then LOW_PORT_CAP="AmbientCapabilities=CAP_NET_BIND_SERVICE"; fi
  $SUDO tee "$UNIT" >/dev/null <<UNITFILE
[Unit]
Description=PipSqueeze VPN Dashboard
After=network.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$GUNICORN_CMD
Restart=always
RestartSec=5
Environment=PATH=$PROJECT_DIR/venv/bin
$LOW_PORT_CAP

[Install]
WantedBy=multi-user.target
UNITFILE
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
  $SUDO systemctl restart "$SERVICE_NAME"
  sleep 2
  if $SUDO systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service '$SERVICE_NAME' is running on $BIND"
  else
    warn "Service did not start cleanly. Check: journalctl -u $SERVICE_NAME -n 50 --no-pager"
  fi
else
  info "Skipped. Start the app with: $GUNICORN_CMD"
fi

# ── Network access (home/office network) ─────────────────────────────────────
if [[ "$MODE" == "lan" ]]; then
  step "Network access"
  if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q '^Status: active'; then
    LAN_NET="$(ip -o -4 addr show 2>/dev/null | awk -v ip="$LAN_IP" '{split($4, a, "/"); if (a[1] == ip) {print $4; exit}}' || true)"
    if [[ -n "$LAN_NET" ]]; then
      LAN_NET="$("$VENV_PY" -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$LAN_NET")"
    fi
    if yesno "The ufw firewall is on. Allow port $PORT from ${LAN_NET:-anywhere}?"; then
      $SUDO ufw allow from "${LAN_NET:-any}" to any port "$PORT" proto tcp
      ok "ufw allows port $PORT from ${LAN_NET:-anywhere}"
    else
      warn "Other devices can't reach the dashboard until port $PORT is allowed through ufw."
    fi
  else
    ok "No active ufw firewall to open"
  fi
  warn "Don't forward port $PORT on your router: the dashboard is plain http, meant for your own network."
fi

# ── nginx reverse proxy + HTTPS (public server) ──────────────────────────────
HTTPS_OK=0
if [[ "$MODE" == "public" ]]; then
  step "Reverse proxy (nginx) and HTTPS"
  SITE="/etc/nginx/sites-available/${SERVICE_NAME}"
  if ! command -v nginx >/dev/null 2>&1; then
    warn "nginx isn't installed, so https://$DOMAIN won't reach the dashboard yet."
    info "Install it ($SUDO apt install nginx certbot python3-certbot-nginx) and re-run ./install.sh."
  else
    SITE_READY=1
    WRITE_SITE=1
    if [[ -f "$SITE" ]]; then
      warn "An nginx site for PipSqueeze already exists ($SITE)."
      info "Replacing it drops anything added since, including certbot's HTTPS settings."
      yesno "Replace it?" "N" || WRITE_SITE=0
    fi
    if [[ "$WRITE_SITE" == "1" ]]; then
      # No /static/ alias: nginx's user often can't read a checkout under /home,
      # which left the dashboard unstyled. Flask serves /static itself.
      $SUDO tee "$SITE" >/dev/null <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass         http://$BIND;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }

    client_max_body_size 10M;
}
NGINX
      $SUDO ln -sf "$SITE" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
      if $SUDO nginx -t; then
        $SUDO systemctl reload nginx
        ok "nginx configured for $DOMAIN"
      else
        SITE_READY=0
        warn "nginx config test failed — not reloading. Fix the config and run: $SUDO nginx -t"
      fi
    else
      ok "Keeping the existing nginx site"
      if $SUDO grep -q "ssl_certificate" "$SITE"; then HTTPS_OK=1; fi
    fi

    if [[ "$SITE_READY" == "1" && "$HTTPS_OK" == "0" ]]; then
      info "certbot needs $DOMAIN's DNS A record pointing at this server, and ports 80 and 443 open."
      if command -v certbot >/dev/null 2>&1 && yesno "Get a free HTTPS certificate for $DOMAIN now (certbot)?"; then
        if $SUDO certbot --nginx -d "$DOMAIN"; then
          HTTPS_OK=1
          ok "HTTPS enabled for https://$DOMAIN"
        else
          warn "certbot failed — re-run later with: $SUDO certbot --nginx -d $DOMAIN"
        fi
      else
        warn "No HTTPS yet. PipSqueeze only sends its login cookie over HTTPS, so you can't log in until it's set up."
        info "Run: $SUDO certbot --nginx -d $DOMAIN"
      fi
    fi
  fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────
printf '\n%s%s── Done ──%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
ok "PipSqueeze is installed."
case "$MODE" in
  public)
    if [[ "$HTTPS_OK" == "1" ]]; then
      info "Dashboard: ${C_BOLD}https://$DOMAIN${C_RESET}"
    else
      info "Dashboard: ${C_BOLD}https://$DOMAIN${C_RESET} (once HTTPS is set up)"
    fi
    ;;
  lan)
    info "Dashboard: ${C_BOLD}http://$LAN_IP:$PORT${C_RESET} (from any device on your network)"
    ;;
  local)
    info "Dashboard: ${C_BOLD}http://127.0.0.1:$PORT${C_RESET}"
    info "From another computer, tunnel in first: ssh -L $PORT:127.0.0.1:$PORT <you>@<this machine>"
    ;;
esac
if google_on && [[ "$MODE" == "public" ]]; then
  info "Login:     Sign in with Google, with an email from GOOGLE_ADMIN_EMAILS or GOOGLE_VIEWER_EMAILS"
elif google_on; then
  warn "Login:     .env turns on Google sign-in, which won't work without HTTPS on a public domain."
  info "           Clear GOOGLE_CLIENT_ID in .env and restart to log in with your password + 2FA code."
else
  info "Login:     your admin username + password + the 6-digit 2FA code"
fi
echo
info "Useful commands:"
info "  $SUDO systemctl status $SERVICE_NAME"
info "  $SUDO journalctl -u $SERVICE_NAME -f"
info "  edit .env then: $SUDO systemctl restart $SERVICE_NAME"
echo
