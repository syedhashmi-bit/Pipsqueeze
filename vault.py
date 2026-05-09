"""
vault.py — at-rest encryption for sensitive notification credentials.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with `MultiFernet` for key rotation.

Key sourcing (in order of preference):
  1. SECRET_VAULT_KEY env var — comma-separated list of Fernet keys.
     The first key is used to encrypt; all are tried for decrypt (key rotation).
  2. Derived from SECRET_KEY via PBKDF2 — single source of truth for new
     deployments where the operator hasn't generated a separate vault key.

Encrypted values stored in the DB are prefixed with `enc:` so legacy plaintext
values can be detected and migrated transparently. New plaintext is encrypted
on first save; existing plaintext is encrypted on first boot via
`migrate_settings()`.
"""

import os
import sqlite3
import base64
from cryptography.fernet import Fernet, MultiFernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DB_FILE = "vpn_dashboard.db"
ENC_PREFIX = "enc:"
SENSITIVE_FIELDS = ("discord_webhook", "email_pass", "telegram_token")
_DERIVED_SALT = b"pipsqueeze-vault-v1"


def _derive_key_from_secret(secret_key: str) -> bytes:
    """Derive a Fernet-compatible key from SECRET_KEY using PBKDF2-HMAC-SHA256."""
    if not secret_key:
        raise RuntimeError(
            "vault: SECRET_KEY must be set (or SECRET_VAULT_KEY explicitly) "
            "to enable at-rest encryption of notification secrets."
        )
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_DERIVED_SALT,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(secret_key.encode("utf-8")))


_fernet_cache = None


def _get_fernet() -> MultiFernet:
    """Build (and cache) a MultiFernet from configured keys."""
    global _fernet_cache
    if _fernet_cache is not None:
        return _fernet_cache

    env_keys = os.getenv("SECRET_VAULT_KEY", "").strip()
    keys = []
    if env_keys:
        for raw in env_keys.split(","):
            raw = raw.strip()
            if raw:
                keys.append(Fernet(raw.encode("ascii")))
    else:
        keys.append(Fernet(_derive_key_from_secret(os.getenv("SECRET_KEY", ""))))

    _fernet_cache = MultiFernet(keys)
    return _fernet_cache


def is_encrypted(s) -> bool:
    return isinstance(s, str) and s.startswith(ENC_PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns 'enc:<token>'. Empty/None passes through."""
    if not plaintext:
        return plaintext or ""
    if is_encrypted(plaintext):
        return plaintext  # idempotent
    f = _get_fernet()
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(maybe_encrypted: str) -> str:
    """Decrypt an 'enc:'-prefixed string. Plaintext or empty passes through."""
    if not maybe_encrypted:
        return maybe_encrypted or ""
    if not is_encrypted(maybe_encrypted):
        return maybe_encrypted  # legacy plaintext
    token = maybe_encrypted[len(ENC_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Either tampered or encrypted under a key we no longer hold.
        # Returning empty avoids leaking ciphertext into UI/SMTP/Discord.
        return ""


def decrypt_settings(row: dict) -> dict:
    out = dict(row)
    for k in SENSITIVE_FIELDS:
        if k in out:
            out[k] = decrypt(out.get(k) or "")
    return out


def encrypt_settings(row: dict) -> dict:
    out = dict(row)
    for k in SENSITIVE_FIELDS:
        if k in out:
            out[k] = encrypt(out.get(k) or "")
    return out


def migrate_settings():
    """One-time: encrypt any plaintext values currently in the DB."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM notifications LIMIT 1").fetchone()
    if not row:
        conn.close()
        return False
    row = dict(row)
    changed = []
    for k in SENSITIVE_FIELDS:
        v = row.get(k) or ""
        if v and not is_encrypted(v):
            new_v = encrypt(v)
            conn.execute(f"UPDATE notifications SET {k}=?", (new_v,))
            changed.append(k)
    conn.commit()
    conn.close()
    return bool(changed)


def rotate_to_primary():
    """Re-encrypt all sensitive fields with the primary key (after key rotation)."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM notifications LIMIT 1").fetchone()
    if not row:
        conn.close()
        return False
    row = dict(row)
    for k in SENSITIVE_FIELDS:
        v = row.get(k) or ""
        if v and is_encrypted(v):
            plain = decrypt(v)
            new_v = encrypt(plain)
            conn.execute(f"UPDATE notifications SET {k}=?", (new_v,))
    conn.commit()
    conn.close()
    return True


def generate_key() -> str:
    """Generate a fresh Fernet key (URL-safe base64). For one-off CLI use."""
    return Fernet.generate_key().decode("ascii")
