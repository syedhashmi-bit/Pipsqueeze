from flask import Flask, render_template, request, send_file, session, redirect, url_for
import subprocess
import os
import json
import re

app = Flask(__name__)

app.secret_key = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"

USERNAME = "admin"
PASSWORD = "CHANGE_THIS_PASSWORD"

SERVER_PUBLIC_KEY = "REPLACE_WITH_SERVER_PUBLIC_KEY"
SERVER_IP = "REPLACE_WITH_YOUR_PUBLIC_IP_OR_DOMAIN"

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

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        pw = request.form["password"]

        if user == USERNAME and pw == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("home"))

        return "Invalid credentials"

    return """
    <form method="post">
        <h2>VPN Dashboard Login</h2>
        <input name="username" placeholder="Username" required>
        <br><br>
        <input name="password" type="password" placeholder="Password" required>
        <br><br>
        <button type="submit">Login</button>
    </form>
    """

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
            return "Invalid client name. Use letters, numbers or underscore only."

        pool = load_ip_pool()

        if client in pool["assigned"]:
            client_ip = pool["assigned"][client]
        else:
            client_ip = get_next_ip(pool)

            if not client_ip:
                return "No available IPs left"

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

    return render_template(
        "index.html",
        config=config,
        public_key=public_key,
        client=client
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)