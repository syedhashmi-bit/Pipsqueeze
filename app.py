from flask import Flask, render_template, request, send_file, session, redirect, url_for, flash, send_from_directory
from dotenv import load_dotenv
import subprocess
import os
import json
import re
import qrcode
import pyotp

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")

USERNAME = os.getenv("APP_USERNAME")
PASSWORD = os.getenv("APP_PASSWORD")
TOTP_SECRET = os.getenv("TOTP_SECRET")

SERVER_PUBLIC_KEY = os.getenv("SERVER_PUBLIC_KEY")
SERVER_IP = os.getenv("SERVER_IP")

IP_POOL_FILE = "ip_pool.json"
BASE_IP = "10.10.0."

def is_valid_client_name(name):
    return re.match(r"^[a-zA-Z0-9_]+$", name)

def load_ip_pool():
    if not os.path.exists(IP_POOL_FILE):
        return {"assigned": {}}

    with open(IP_POOL_FILE, "r") as f:
        return json.load(f)

def save_ip_pool(pool):
    with open(IP_POOL_FILE, "w") as f:
        json.dump(pool, f, indent=4)

def get_next_ip(pool):
    used_ips = set(pool["assigned"].values())

    for i in range(2, 255):
        ip = BASE_IP + str(i)

        if ip not in used_ips:
            return ip

    return None

def get_clients():
    pool = load_ip_pool()
    clients = []

    for name, ip in pool["assigned"].items():
        clients.append({
            "name": name,
            "ip": ip,
            "config_exists": os.path.exists(f"clients/{name}.conf"),
            "qr_exists": os.path.exists(f"qr_codes/{name}.png"),
            "key_exists": os.path.exists(f"keys/{name}_public.key")
        })

    return clients

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        user = request.form["username"]
        pw = request.form["password"]
        code = request.form["code"]

        if user == USERNAME and pw == PASSWORD:
            totp = pyotp.TOTP(TOTP_SECRET)

            if totp.verify(code):
                session["logged_in"] = True
                return redirect(url_for("home"))

            error = "Invalid 2FA code"
        else:
            error = "Invalid username or password"

    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/", methods=["GET", "POST"])
def home():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    config = None
    public_key = None
    client = None

    if request.method == "POST":
        client = request.form["client"].strip()

        if not is_valid_client_name(client):
            flash("Invalid client name. Use letters, numbers or underscore only.")
            return redirect(url_for("home"))

        pool = load_ip_pool()

        if client in pool["assigned"]:
            client_ip = pool["assigned"][client]
        else:
            client_ip = get_next_ip(pool)

            if not client_ip:
                flash("No available IPs left.")
                return redirect(url_for("home"))

            pool["assigned"][client] = client_ip
            save_ip_pool(pool)

        private_key = subprocess.check_output("wg genkey", shell=True).decode().strip()
        public_key = subprocess.check_output(
            f"echo {private_key} | wg pubkey",
            shell=True
        ).decode().strip()

        config = f"""
[Interface]
PrivateKey = {private_key}
Address = {client_ip}/24
DNS = 1.1.1.1

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {SERVER_IP}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""

        os.makedirs("clients", exist_ok=True)
        with open(f"clients/{client}.conf", "w") as f:
            f.write(config)

        os.makedirs("keys", exist_ok=True)
        with open(f"keys/{client}_public.key", "w") as f:
            f.write(public_key)

        os.makedirs("qr_codes", exist_ok=True)
        qr = qrcode.make(config)
        qr.save(f"qr_codes/{client}.png")

        flash(f"Client {client} created successfully.")

    clients = get_clients()
    total_clients = len(clients)
    used_ips = total_clients
    available_ips = 253 - used_ips

    return render_template(
        "index.html",
        config=config,
        public_key=public_key,
        client=client,
        clients=clients,
        total_clients=total_clients,
        used_ips=used_ips,
        available_ips=available_ips
    )

@app.route("/download/<client>")
def download(client):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_valid_client_name(client):
        return "Invalid client name"

    path = f"clients/{client}.conf"

    if not os.path.exists(path):
        return "Config file not found"

    return send_file(path, as_attachment=True)

@app.route("/qr/<client>")
def qr_code(client):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_valid_client_name(client):
        return "Invalid client name"

    path = f"qr_codes/{client}.png"

    if not os.path.exists(path):
        return "QR code not found"

    return send_from_directory("qr_codes", f"{client}.png")

@app.route("/delete/<client>", methods=["POST"])
def delete_client(client):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if not is_valid_client_name(client):
        flash("Invalid client name.")
        return redirect(url_for("home"))

    pool = load_ip_pool()

    if client in pool["assigned"]:
        del pool["assigned"][client]
        save_ip_pool(pool)

    files_to_delete = [
        f"clients/{client}.conf",
        f"keys/{client}_public.key",
        f"qr_codes/{client}.png"
    ]

    for file_path in files_to_delete:
        if os.path.exists(file_path):
            os.remove(file_path)

    flash(f"Client {client} deleted successfully.")
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)