"""
Set up a Clash of Clans API key whitelisted for Render.

Two ways to use this:

1) ONE-TIME, RECOMMENDED: whitelist Render's static outbound IP range so you
   never have to touch this again.
   - In the Render dashboard: your service -> Connect (top right) -> Outbound
     tab. Copy the IP(s)/CIDR range shown there (e.g. "44.233.151.27,
     35.160.120.126, 34.211.200.85" or an "x.x.x.0/24" block, depending on
     region).
   - Run:
       COC_EMAIL=you@example.com COC_PASSWORD=yourpassword \
       COC_CIDR_RANGES="44.233.151.27/32,35.160.120.126/32,34.211.200.85/32" \
       python scripts/refresh_coc_key.py
   - Paste the printed token into Render's COC_API_TOKEN once. Done - no
     more regenerating on every deploy, unless Render changes its range
     (rare; Render will announce it if so).

2) FALLBACK, if you don't have a static range (e.g. legacy pre-2022 Oregon
   workspace, or you're just testing from your own machine): whitelist
   whatever IP this script is currently running from.
       COC_EMAIL=you@example.com COC_PASSWORD=yourpassword python scripts/refresh_coc_key.py

Why this exists: developer.clashofclans.com locks each API key to specific
IP addresses/ranges. This script uses the same login+key-management
endpoints the developer.clashofclans.com website itself calls (the same
approach community libraries such as coc.py use for their
`login(email, password)` helper). It is not officially documented as an API,
so treat it as best-effort - if Supercell changes these endpoints this script
may need updating.

NEVER commit your Supercell email/password or the printed token to git.
"""

import os
import sys

import requests

LOGIN_URL = "https://developer.clashofclans.com/api/login"
LIST_URL = "https://developer.clashofclans.com/api/apikey/list"
CREATE_URL = "https://developer.clashofclans.com/api/apikey/create"
REVOKE_URL = "https://developer.clashofclans.com/api/apikey/revoke"
IP_URL = "https://api.ipify.org"

KEY_NAME = os.getenv("COC_KEY_NAME", "clash-of-clans-progress-tracker")


def get_current_ip():
    resp = requests.get(IP_URL, timeout=10)
    resp.raise_for_status()
    return resp.text.strip()


def resolve_cidr_ranges():
    """
    COC_CIDR_RANGES, comma-separated (e.g. Render's static outbound range),
    each normalized to include a /prefix. Falls back to this machine's
    current single IP as a /32 if the variable isn't set.
    """
    raw = os.getenv("COC_CIDR_RANGES", "").strip()
    if raw:
        ranges = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            ranges.append(part if "/" in part else f"{part}/32")
        return ranges
    return [f"{get_current_ip()}/32"]


def login(session, email, password):
    resp = session.post(LOGIN_URL, json={"email": email, "password": password}, timeout=15)
    if resp.status_code != 200:
        raise SystemExit(
            f"Login failed (HTTP {resp.status_code}). Check COC_EMAIL / COC_PASSWORD.\n"
            f"Response: {resp.text[:300]}"
        )


def find_existing_key(session, name):
    resp = session.post(LIST_URL, timeout=15)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    for key in keys:
        if key.get("name") == name:
            return key
    return None


def revoke_key(session, key_id):
    session.post(REVOKE_URL, json={"id": key_id}, timeout=15)


def create_key(session, name, cidr_ranges):
    resp = session.post(
        CREATE_URL,
        json={
            "name": name,
            "description": "Auto-managed by refresh_coc_key.py",
            "cidrRanges": cidr_ranges,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"Key creation failed (HTTP {resp.status_code}). "
            f"You may have hit the 10-key-per-account limit - delete unused keys at "
            f"developer.clashofclans.com and try again.\nResponse: {resp.text[:300]}"
        )
    return resp.json()["key"]["key"]


def main():
    email = os.getenv("COC_EMAIL", "").strip()
    password = os.getenv("COC_PASSWORD", "").strip()
    if not email or not password:
        sys.exit(
            "Set COC_EMAIL and COC_PASSWORD environment variables first "
            "(your developer.clashofclans.com login, not the API token)."
        )

    cidr_ranges = resolve_cidr_ranges()
    print(f"Whitelisting: {', '.join(cidr_ranges)}")

    session = requests.Session()
    login(session, email, password)

    existing = find_existing_key(session, KEY_NAME)
    if existing:
        print(f"Revoking previous key '{KEY_NAME}' (id={existing['id']}) ...")
        revoke_key(session, existing["id"])

    print(f"Creating key '{KEY_NAME}' ...")
    token = create_key(session, KEY_NAME, cidr_ranges)

    print("\nDone. New COC_API_TOKEN:\n")
    print(token)
    print(
        "\nUpdate this in Render: dashboard -> your service -> Environment -> "
        "COC_API_TOKEN, then restart the service.\n"
        "If you whitelisted Render's static outbound range, you shouldn't need "
        "to run this again unless Render changes that range."
    )


if __name__ == "__main__":
    main()