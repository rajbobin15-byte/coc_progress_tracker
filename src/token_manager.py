"""
Keeps a Clash of Clans API key valid automatically, even when the app's
outbound IP address changes on every restart/redeploy (e.g. Render's free
plan, where the container gets a new IP each time it wakes up).

How it works
------------
Supercell's key-management endpoint only reliably accepts a single IP per
key (a /32) - not a wide CIDR block from an unrelated machine, which is why
whitelisting "the whole Render range" up front doesn't work. So instead:

1. Ask https://api.ipify.org what this process's current public IP is.
2. Log in to developer.clashofclans.com the same way the website itself
   does (this is the same undocumented-but-widely-used flow several
   community CoC libraries rely on - see scripts/refresh_coc_key.py, which
   this reuses the same endpoints as).
3. Revoke any previous key with the same name (so we never hit the
   10-key-per-account limit) and create a fresh key scoped to the current IP.
4. Save the new token in the database (Postgres via DATABASE_URL if set,
   else the local SQLite file) so every process/reader shares it and it
   survives restarts.

coc_api.py calls `ensure_token()` before a request, and again with
`force_refresh=True` if a request comes back 403 - so a stale token heals
itself on the very next call instead of needing anyone to notice and paste
in a new one.

This only activates if COC_EMAIL and COC_PASSWORD (your
developer.clashofclans.com login, NOT the API token) are set as environment
variables. If they are not set, `auto_refresh_enabled()` returns False and
coc_api.py falls back to the static COC_API_TOKEN env var exactly as before.

NEVER commit your Supercell email/password to git - set them as Render
Environment Variables (or secrets), same as COC_API_TOKEN today.
"""

import os
from contextlib import closing
from datetime import datetime, timezone

import requests

from src.database import USING_POSTGRES, dialect_schema, get_connection

LOGIN_URL = "https://developer.clashofclans.com/api/login"
LIST_URL = "https://developer.clashofclans.com/api/apikey/list"
CREATE_URL = "https://developer.clashofclans.com/api/apikey/create"
REVOKE_URL = "https://developer.clashofclans.com/api/apikey/revoke"
IP_URL = "https://api.ipify.org"
REQUEST_TIMEOUT = 15

KEY_NAME = os.getenv("COC_KEY_NAME", "clash-of-clans-progress-tracker")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_tokens (
    key_name   TEXT PRIMARY KEY,
    token      TEXT NOT NULL,
    ip_address TEXT,
    updated_at TEXT
);
"""


class TokenRefreshError(Exception):
    """Raised when we can't mint or store a fresh token."""


def auto_refresh_enabled():
    return bool(os.getenv("COC_EMAIL", "").strip() and os.getenv("COC_PASSWORD", "").strip())


# ---------------------------------------------------------------- storage

def _init_table():
    with closing(get_connection()) as conn:
        with conn:
            conn.executescript(dialect_schema(_SCHEMA))


def _load_stored_token():
    _init_table()
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT token, ip_address FROM api_tokens WHERE key_name = ?",
            (KEY_NAME,),
        ).fetchone()
    if not row:
        return None, None
    return row["token"], row["ip_address"]


def _save_token(token, ip_address):
    _init_table()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with closing(get_connection()) as conn:
        with conn:
            if USING_POSTGRES:
                conn.execute(
                    """
                    INSERT INTO api_tokens (key_name, token, ip_address, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (key_name) DO UPDATE
                        SET token = EXCLUDED.token,
                            ip_address = EXCLUDED.ip_address,
                            updated_at = EXCLUDED.updated_at
                    """,
                    (KEY_NAME, token, ip_address, now),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO api_tokens "
                    "(key_name, token, ip_address, updated_at) VALUES (?, ?, ?, ?)",
                    (KEY_NAME, token, ip_address, now),
                )


# ------------------------------------------------------------- Supercell

def _current_ip():
    try:
        resp = requests.get(IP_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text.strip()
    except requests.exceptions.RequestException as exc:
        raise TokenRefreshError(f"Could not determine current public IP: {exc}")


def _mint_token(ip_address):
    email = os.getenv("COC_EMAIL", "").strip()
    password = os.getenv("COC_PASSWORD", "").strip()

    session = requests.Session()
    try:
        resp = session.post(
            LOGIN_URL, json={"email": email, "password": password}, timeout=REQUEST_TIMEOUT
        )
    except requests.exceptions.RequestException as exc:
        raise TokenRefreshError(f"Could not reach developer.clashofclans.com: {exc}")
    if resp.status_code != 200:
        raise TokenRefreshError(
            f"developer.clashofclans.com login failed (HTTP {resp.status_code}). "
            "Check COC_EMAIL / COC_PASSWORD."
        )

    resp = session.post(LIST_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    for key in resp.json().get("keys", []):
        if key.get("name") == KEY_NAME:
            session.post(REVOKE_URL, json={"id": key["id"]}, timeout=REQUEST_TIMEOUT)
            break

    resp = session.post(
        CREATE_URL,
        json={
            "name": KEY_NAME,
            "description": f"Auto-minted by token_manager.py for {ip_address}",
            "cidrRanges": [f"{ip_address}/32"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        raise TokenRefreshError(
            f"Clash of Clans key creation failed (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()["key"]["key"]


# ---------------------------------------------------------------- public

def get_stored_token():
    """The token currently saved in the database (manually entered or
    auto-minted), or None if nothing is stored yet."""
    token, _ = _load_stored_token()
    return token


def save_manual_token(token):
    """Save a token a human pasted in (e.g. after being shown the current
    IP) so every future request uses it - until it, too, gets rejected."""
    token = (token or "").strip()
    if not token:
        raise TokenRefreshError("No token was entered.")
    try:
        ip_address = _current_ip()
    except TokenRefreshError:
        ip_address = "unknown"
    _save_token(token, ip_address)


def safe_current_ip():
    """Best-effort current public IP, for display in error messages - never
    raises, since this is called while we're already handling an error."""
    try:
        return _current_ip()
    except TokenRefreshError:
        return "unknown (could not reach api.ipify.org)"


def ensure_token(force_refresh=False):
    """
    Return a token that should work for this process's current IP.

    force_refresh=False (the default, cheap path): return whatever token is
    already stored, with no network calls to Supercell at all.

    force_refresh=True: always mint a brand-new key for the current IP and
    persist it - call this after a request comes back 403, since that means
    the stored token no longer matches the current outbound IP.
    """
    if not auto_refresh_enabled():
        return None

    if not force_refresh:
        token, _ = _load_stored_token()
        if token:
            return token

    ip_address = _current_ip()
    token = _mint_token(ip_address)
    _save_token(token, ip_address)
    return token
