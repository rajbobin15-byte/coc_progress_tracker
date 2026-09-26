"""
Clash of Clans official API client.

All communication with https://api.clashofclans.com lives in this file.
Credentials are read from the local .env file, never from source code.
"""

import os
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

BASE_URL = "https://api.clashofclans.com/v1"
REQUEST_TIMEOUT = 15  # seconds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# Characters Clash of Clans allows in tags (after the '#').
VALID_TAG_CHARS = set("0289PYLQGRJCUV")
PLACEHOLDER_TOKEN = "paste_your_token_here"


# ---------------------------------------------------------------- errors

class ClashAPIError(Exception):
    """Base class. `title` is a short heading the UI can display."""
    title = "Clash API error"


class ConfigError(ClashAPIError):
    title = "Configuration problem"


class InvalidTokenError(ClashAPIError):
    title = "Invalid token or IP address"


class NotFoundError(ClashAPIError):
    title = "Clan not found"


class RateLimitError(ClashAPIError):
    title = "Rate limit reached"


class ServiceUnavailableError(ClashAPIError):
    title = "Clash API unavailable"


class NetworkError(ClashAPIError):
    title = "Network error"


# ---------------------------------------------------------------- config

def normalize_tag(raw_tag, label="tag"):
    """Return a clean tag like '#2PP'. Raises ConfigError if it is malformed."""
    body = raw_tag.strip().upper().lstrip("#").replace("O", "0")
    if not body or any(ch not in VALID_TAG_CHARS for ch in body):
        raise ConfigError(
            f"'{raw_tag}' is not a valid {label}. Tags contain only the "
            "characters 0289PYLQGRJCUV (after the #). Check it in-game."
        )
    return "#" + body


def normalize_clan_tag(raw_tag):
    """Return a clean clan tag like '#2PP'. Raises ConfigError if malformed."""
    return normalize_tag(raw_tag, label="clan tag")


def load_config():
    """
    Read COC_API_TOKEN and CLAN_TAG.

    Locally, these come from a .env file next to the project (loaded here).
    On a host like Render there is no .env file - the same variable names are
    set directly in the platform's environment, and load_dotenv() is a no-op
    if the file doesn't exist, so os.getenv() below picks them up either way.
    """
    if ENV_PATH.exists():
        # override=True so edits to .env take effect without restarting Streamlit.
        load_dotenv(ENV_PATH, override=True)

    token = os.getenv("COC_API_TOKEN", "").strip()
    raw_tag = os.getenv("CLAN_TAG", "").strip()

    if not token or token == PLACEHOLDER_TOKEN:
        raise ConfigError(
            "COC_API_TOKEN is missing. Locally: add it to your .env file. "
            "On Render: set it as an Environment Variable in the service settings."
        )
    if not raw_tag:
        raise ConfigError(
            "CLAN_TAG is missing. Locally: add it to your .env file. "
            "On Render: set it as an Environment Variable in the service settings."
        )

    return token, normalize_clan_tag(raw_tag)


# ---------------------------------------------------------------- HTTP

def _error_details(response):
    """Extract (reason, message) from an API error body, if there is one."""
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if isinstance(body, dict):
        return body.get("reason", ""), body.get("message", "")
    return "", ""


def _get(path, token):
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        raise NetworkError(
            f"The request timed out after {REQUEST_TIMEOUT} seconds. "
            "Check your internet connection and try again."
        )
    except requests.exceptions.ConnectionError:
        raise NetworkError(
            "Could not connect to api.clashofclans.com. Check your internet "
            "connection, VPN, proxy or firewall settings."
        )
    except requests.exceptions.RequestException as exc:
        raise NetworkError(f"Unexpected network problem: {type(exc).__name__}")

    status = response.status_code

    if status == 200:
        try:
            return response.json()
        except ValueError:
            raise ClashAPIError("The API returned a response that is not valid JSON.")

    reason, message = _error_details(response)
    api_said = f"\n\nAPI response: {reason} - {message}" if (reason or message) else ""

    if status == 403:
        raise InvalidTokenError(
            "The API rejected your credentials. Either COC_API_TOKEN is wrong "
            "or expired, or your key is not allowed for your current public IP "
            "address. Keys only work from the IP addresses entered when the key "
            "was created. Find your current IP with 'curl https://api.ipify.org' "
            "and compare it to the key at developer.clashofclans.com."
            + api_said
        )
    if status == 404:
        raise NotFoundError(
            "No clan exists with that tag. Check CLAN_TAG in your .env file."
            + api_said
        )
    if status == 400:
        raise ClashAPIError(
            "The API rejected the request as malformed. Check the format of "
            "CLAN_TAG in your .env file." + api_said
        )
    if status == 429:
        raise RateLimitError(
            "Too many requests were sent to the API. Wait a minute and try again."
            + api_said
        )
    if status == 503:
        raise ServiceUnavailableError(
            "The Clash of Clans API is down for maintenance. Try again later."
            + api_said
        )

    raise ClashAPIError(f"Unexpected API response (HTTP {status})." + api_said)


# ---------------------------------------------------------------- public API

def get_clan_info():
    """Fetch the configured clan (GET /clans/{clanTag}) and return the JSON dict."""
    token, clan_tag = load_config()
    encoded_tag = quote(clan_tag, safe="")  # '#' must be sent as %23
    return _get(f"/clans/{encoded_tag}", token)


# =====================================================================
# Step 4 additions: wars, Clan War League and Clan Capital
# =====================================================================

class WarLogPrivateError(ClashAPIError):
    title = "War data not accessible"


def _tag_path(tag):
    return quote(tag, safe="")  # '#' must be sent as %23


def _get_war_data(path, token):
    """Like _get, but explains that a 403 here usually means a private war log."""
    try:
        return _get(path, token)
    except InvalidTokenError:
        raise WarLogPrivateError(
            "The API refused access to this war data (HTTP 403). Your token "
            "already works for basic clan information, so the most likely cause "
            "is that your clan's war log is set to private. A leader or co-leader "
            "can make it public in the game: Clan Settings > War Log > Public. "
            "Less likely: your public IP address changed since the key was created."
        )


def get_current_war():
    """GET /clans/{tag}/currentwar. state may be notInWar, preparation, inWar, warEnded."""
    token, clan_tag = load_config()
    return _get_war_data(f"/clans/{_tag_path(clan_tag)}/currentwar", token)


def get_war_log_items(limit=50):
    """GET /clans/{tag}/warlog. Returns the list of finished wars (clan-level only)."""
    token, clan_tag = load_config()
    data = _get_war_data(f"/clans/{_tag_path(clan_tag)}/warlog?limit={int(limit)}", token)
    return data.get("items", [])


def get_cwl_group():
    """GET /clans/{tag}/currentwar/leaguegroup. Returns None if not in CWL right now."""
    token, clan_tag = load_config()
    try:
        return _get_war_data(
            f"/clans/{_tag_path(clan_tag)}/currentwar/leaguegroup", token
        )
    except NotFoundError:
        return None


def get_cwl_war(war_tag):
    """GET /clanwarleagues/wars/{warTag}. One CWL war between two clans."""
    token, _ = load_config()
    return _get_war_data(f"/clanwarleagues/wars/{_tag_path(war_tag)}", token)


def get_capital_raid_seasons(limit=20):
    """GET /clans/{tag}/capitalraidseasons. Returns the list of raid weekends."""
    token, clan_tag = load_config()
    data = _get(f"/clans/{_tag_path(clan_tag)}/capitalraidseasons?limit={int(limit)}", token)
    return data.get("items", [])


# =====================================================================
# Player profile (individual player detail - not clan-scoped)
# =====================================================================

def get_player_info(raw_player_tag):
    """
    GET /players/{playerTag}. Returns the full player profile: heroes, hero
    equipment, troops, spells, pets, achievements, trophies, war stars, etc.
    Works for ANY public player tag, not just members of the configured clan.
    """
    token, _ = load_config()
    tag = normalize_tag(raw_player_tag, label="player tag")
    try:
        return _get(f"/players/{_tag_path(tag)}", token)
    except NotFoundError:
        raise NotFoundError(
            f"No player exists with tag {tag}. Double-check it in-game under "
            "your profile (it's different from your clan's tag)."
        )