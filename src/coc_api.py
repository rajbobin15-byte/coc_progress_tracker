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

from src import token_manager

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


class ManualTokenRequired(ClashAPIError):
    """
    Raised when the token in use was rejected and nothing could refresh it
    automatically. Carries current_ip so the caller (a Streamlit page) can
    show it and offer a box to paste a freshly created token into.
    """
    title = "New token needed"

    def __init__(self, message, current_ip=None):
        super().__init__(message)
        self.current_ip = current_ip


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
    Read CLAN_TAG (and, unless token auto-refresh is configured, COC_API_TOKEN).

    Locally, these come from a .env file next to the project (loaded here).
    On a host like Render there is no .env file - the same variable names are
    set directly in the platform's environment, and load_dotenv() is a no-op
    if the file doesn't exist, so os.getenv() below picks them up either way.

    The returned token is informational/backwards-compatible only: the
    actual token used for requests is resolved per-call by _resolve_token(),
    so it can be refreshed automatically without callers needing to re-read
    config. If COC_EMAIL/COC_PASSWORD are set, the token slot here is simply
    None - it gets minted lazily on first use.
    """
    if ENV_PATH.exists():
        # override=True so edits to .env take effect without restarting Streamlit.
        load_dotenv(ENV_PATH, override=True)

    raw_tag = os.getenv("CLAN_TAG", "").strip()
    if not raw_tag:
        raise ConfigError(
            "CLAN_TAG is missing. Locally: add it to your .env file. "
            "On Render: set it as an Environment Variable in the service settings."
        )

    if token_manager.auto_refresh_enabled():
        token = None
    else:
        token = os.getenv("COC_API_TOKEN", "").strip()
        if not token or token == PLACEHOLDER_TOKEN:
            raise ConfigError(
                "COC_API_TOKEN is missing, and automatic refresh isn't configured "
                "either. Locally: add COC_API_TOKEN to your .env file. On Render: "
                "set it as an Environment Variable, or set COC_EMAIL/COC_PASSWORD "
                "to mint and refresh tokens automatically instead."
            )

    return token, normalize_clan_tag(raw_tag)


def _resolve_token(force_refresh=False):
    """
    Return a token to use right now, in priority order:
    1. A token saved in the database - whether a human pasted it in after
       being shown the current IP, or a previous auto-refresh minted it.
       This always wins once present, since it's the most recently
       known-good token for wherever this app is currently running.
    2. If COC_EMAIL/COC_PASSWORD are set, mint one automatically.
    3. The static COC_API_TOKEN env var, as a last resort.
    force_refresh=True skips the stored token (it just proved invalid) and
    tries to mint a fresh one automatically; if that isn't configured or
    fails, the caller is expected to fall back to asking a human.
    """
    if not force_refresh:
        stored = token_manager.get_stored_token()
        if stored:
            return stored

    auto_refresh_error = None
    if token_manager.auto_refresh_enabled():
        try:
            token = token_manager.ensure_token(force_refresh=force_refresh)
            if token:
                return token
        except token_manager.TokenRefreshError as exc:
            auto_refresh_error = str(exc)

    token = os.getenv("COC_API_TOKEN", "").strip()
    if token and token != PLACEHOLDER_TOKEN:
        return token

    if auto_refresh_error:
        raise ConfigError(
            "Automatic token refresh failed, and no static COC_API_TOKEN is set "
            f"as a fallback. Underlying error: {auto_refresh_error}"
        )
    raise ConfigError(
        "No usable COC_API_TOKEN, and automatic refresh (COC_EMAIL/COC_PASSWORD) "
        "is not configured."
    )


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


def _proxies():
    """
    Optional forward proxy with a fixed IP (see COC_PROXY_URL), used so the
    outbound IP Supercell sees is always the same, regardless of what IP
    Render itself is using for this particular connection.
    Format: http://user:pass@proxy-host:port
    """
    proxy_url = os.getenv("COC_PROXY_URL", "").strip()
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _get(path, token):
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(
            url, headers=headers, timeout=REQUEST_TIMEOUT, proxies=_proxies()
        )
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


def _get_with_retry(path):
    """
    Like _get, but if the token is rejected (403): try auto-refresh first
    (if COC_EMAIL/COC_PASSWORD are configured); if that isn't available or
    doesn't work either, raise ManualTokenRequired with the current outbound
    IP so a human can create a token for it and paste it in.
    """
    token = _resolve_token()
    try:
        return _get(path, token)
    except InvalidTokenError:
        pass

    if token_manager.auto_refresh_enabled():
        try:
            token = _resolve_token(force_refresh=True)
            return _get(path, token)
        except InvalidTokenError:
            pass

    ip_address = token_manager.safe_current_ip()
    raise ManualTokenRequired(
        "The Clash of Clans API rejected the current token (HTTP 403). This "
        f"process's current outbound IP is **{ip_address}**. Create or update "
        "a key for this IP at developer.clashofclans.com, then paste the new "
        "token in below - it will be saved and used for every request from "
        "now on.",
        current_ip=ip_address,
    )


# ---------------------------------------------------------------- public API

def get_clan_info():
    """Fetch the configured clan (GET /clans/{clanTag}) and return the JSON dict."""
    _, clan_tag = load_config()
    encoded_tag = quote(clan_tag, safe="")  # '#' must be sent as %23
    return _get_with_retry(f"/clans/{encoded_tag}")


# =====================================================================
# Step 4 additions: wars, Clan War League and Clan Capital
# =====================================================================

class WarLogPrivateError(ClashAPIError):
    title = "War data not accessible"


def _tag_path(tag):
    return quote(tag, safe="")  # '#' must be sent as %23


def _get_war_data(path):
    """Like _get_with_retry, but explains that a 403 here usually means a private war log."""
    try:
        return _get_with_retry(path)
    except ManualTokenRequired as exc:
        raise WarLogPrivateError(
            "The API refused access to this war data (HTTP 403), even after "
            "trying to refresh the token. Your token already works for basic "
            "clan information, so the most likely cause is that your clan's "
            "war log is set to private. A leader or co-leader can make it "
            "public in the game: Clan Settings > War Log > Public. Less "
            "likely: the token needs updating for this process's current "
            f"outbound IP ({exc.current_ip}) - the Dashboard's 'Test API "
            "Connection' button will prompt for a new token if that's the case."
        )


def get_current_war():
    """GET /clans/{tag}/currentwar. state may be notInWar, preparation, inWar, warEnded."""
    _, clan_tag = load_config()
    return _get_war_data(f"/clans/{_tag_path(clan_tag)}/currentwar")


def get_war_log_items(limit=50):
    """GET /clans/{tag}/warlog. Returns the list of finished wars (clan-level only)."""
    _, clan_tag = load_config()
    data = _get_war_data(f"/clans/{_tag_path(clan_tag)}/warlog?limit={int(limit)}")
    return data.get("items", [])


def get_cwl_group():
    """GET /clans/{tag}/currentwar/leaguegroup. Returns None if not in CWL right now."""
    _, clan_tag = load_config()
    try:
        return _get_war_data(f"/clans/{_tag_path(clan_tag)}/currentwar/leaguegroup")
    except NotFoundError:
        return None


def get_cwl_war(war_tag):
    """GET /clanwarleagues/wars/{warTag}. One CWL war between two clans."""
    load_config()
    return _get_war_data(f"/clanwarleagues/wars/{_tag_path(war_tag)}")


def get_capital_raid_seasons(limit=20):
    """GET /clans/{tag}/capitalraidseasons. Returns the list of raid weekends."""
    _, clan_tag = load_config()
    data = _get_with_retry(f"/clans/{_tag_path(clan_tag)}/capitalraidseasons?limit={int(limit)}")
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
    load_config()
    tag = normalize_tag(raw_player_tag, label="player tag")
    try:
        return _get_with_retry(f"/players/{_tag_path(tag)}")
    except NotFoundError:
        raise NotFoundError(
            f"No player exists with tag {tag}. Double-check it in-game under "
            "your profile (it's different from your clan's tag)."
        )