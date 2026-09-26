"""
Data collection without any Streamlit dependency.

Reuses the existing API client (coc_api.py) and storage functions
(database.py, analytics_db.py). The API token is read by coc_api.py from .env
and is never logged or printed here.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone

from src.analytics_db import (
    get_finished_cwl_war_tags,
    init_analytics_db,
    save_capital_raids,
    save_clan_meta,
    save_cwl_group,
    save_cwl_war,
    save_war,
    save_war_log,
)
from src.coc_api import (
    ClashAPIError,
    ConfigError,
    InvalidTokenError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
    WarLogPrivateError,
    get_capital_raid_seasons,
    get_clan_info,
    get_current_war,
    get_cwl_group,
    get_cwl_war,
    get_war_log_items,
    load_config,
)
from src.database import get_latest_snapshot, init_db, save_snapshot

log = logging.getLogger("collector")

TASKS = ("snapshot", "wars", "cwl", "capital")

EXIT_OK = 0
EXIT_PARTIAL = 1  # at least one task failed, others may have succeeded
EXIT_FATAL = 2    # configuration, token/IP, network or rate-limit problem

# After these errors every further API call would fail too, so we stop.
FATAL_ERRORS = (
    ConfigError,
    InvalidTokenError,
    NetworkError,
    RateLimitError,
    ServiceUnavailableError,
)

PRIVATE_LOG_MESSAGE = (
    "war log is private, so the API returns no war data "
    "(game: Clan Settings > War Log > Public)"
)


def _one_line(exc):
    return " ".join(str(exc).split())


def _minutes_since(iso_utc):
    then = datetime.fromisoformat(iso_utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60


# ------------------------------------------------------------------ tasks
# Each task returns (status, message) where status is "ok" or "skipped".

def _task_snapshot(clan, clan_tag, force, interval_minutes):
    latest = get_latest_snapshot(clan_tag)
    if latest and not force:
        age = _minutes_since(latest["taken_at"])
        if age < interval_minutes:
            return "skipped", (
                f"latest snapshot #{latest['id']} is only {age:.0f} min old "
                f"(minimum {interval_minutes} min; --force overrides)"
            )
    snapshot_id = save_snapshot(clan)
    save_clan_meta(clan)
    members = len(clan.get("memberList", []))
    return "ok", f"snapshot #{snapshot_id} saved with {members} members"


def _task_wars(clan, clan_tag):
    if clan.get("isWarLogPublic") is False:
        return "skipped", PRIVATE_LOG_MESSAGE

    parts = []
    war = get_current_war()
    state = war.get("state")
    if state in (None, "notInWar"):
        parts.append("no regular war in progress")
    else:
        war_id = save_war(war, clan_tag)
        opponent = (war.get("opponent") or {}).get("name", "unknown")
        if war_id is None:
            parts.append("API returned a war that does not include our clan")
        else:
            parts.append(f"current war saved (vs {opponent}, state {state})")

    items = get_war_log_items()
    new = save_war_log(clan_tag, items)
    parts.append(f"war log: {len(items)} read, {new} new")
    return "ok", "; ".join(parts)


def _task_cwl(clan, clan_tag):
    if clan.get("isWarLogPublic") is False:
        return "skipped", PRIVATE_LOG_MESSAGE

    group = get_cwl_group()
    if group is None:
        return "skipped", "not in a CWL group right now"

    season = group.get("season")
    league = (clan.get("warLeague") or {}).get("name")
    season_id = save_cwl_group(group, clan_tag, league)
    if season_id is None:
        return "skipped", "league group had no season, nothing saved"

    known_final = get_finished_cwl_war_tags()
    todo = [
        (round_no, tag)
        for round_no, rnd in enumerate(group.get("rounds", []), start=1)
        for tag in rnd.get("warTags", [])
        if tag and tag != "#0"  # '#0' = round not decided yet
    ]
    to_fetch = [item for item in todo if item[1] not in known_final]

    saved = 0
    for round_no, tag in to_fetch:
        try:
            war = get_cwl_war(tag)
        except NotFoundError:
            continue
        save_cwl_war(season_id, season, round_no, tag, war, clan_tag)
        saved += 1
        time.sleep(0.2)  # be gentle with the API

    return "ok", (
        f"season {season}: {len(todo)} wars known, {saved} fetched or updated, "
        f"{len(todo) - len(to_fetch)} already final"
    )


def _task_capital(clan_tag):
    items = get_capital_raid_seasons(limit=20)
    if not items:
        return "skipped", "the API returned no raid weekends"
    total, new = save_capital_raids(clan_tag, items)
    return "ok", f"{total} raid weekends read, {new} new"


# ----------------------------------------------------------------- runner

def run_collection(tasks=TASKS, force=False, snapshot_interval_minutes=60):
    """Run the selected tasks once. Returns an exit code (EXIT_*)."""
    init_db()
    init_analytics_db()

    try:
        _, clan_tag = load_config()
        clan = get_clan_info()
    except ClashAPIError as exc:
        log.error("%s: %s", exc.title, _one_line(exc))
        return EXIT_FATAL

    log.info(
        "Connected: %s (%s), %s members",
        clan.get("name"), clan.get("tag"), clan.get("members"),
    )

    jobs = {
        "snapshot": lambda: _task_snapshot(clan, clan_tag, force, snapshot_interval_minutes),
        "wars": lambda: _task_wars(clan, clan_tag),
        "cwl": lambda: _task_cwl(clan, clan_tag),
        "capital": lambda: _task_capital(clan_tag),
    }

    outcome = {name: "not run" for name in tasks}
    fatal = False

    for name in tasks:
        if fatal:
            break
        try:
            status, message = jobs[name]()
        except WarLogPrivateError:
            status, message = "skipped", "the API refused war data (HTTP 403), most likely a private war log"
        except FATAL_ERRORS as exc:
            log.error("%s: FAILED - %s: %s", name, exc.title, _one_line(exc))
            outcome[name] = "failed"
            fatal = True
            continue
        except ClashAPIError as exc:
            log.error("%s: FAILED - %s: %s", name, exc.title, _one_line(exc))
            outcome[name] = "failed"
            continue
        except sqlite3.Error as exc:
            log.error("%s: FAILED - database error: %s", name, exc)
            outcome[name] = "failed"
            continue
        except Exception as exc:  # keep the scheduled job from dying silently
            log.error("%s: FAILED - unexpected %s: %s", name, type(exc).__name__, exc, exc_info=True)
            outcome[name] = "failed"
            continue

        outcome[name] = status
        level = logging.INFO if status == "ok" else logging.WARNING
        log.log(level, "%s: %s - %s", name, status.upper(), message)

    log.info("Summary: %s", " ".join(f"{k}={v}" for k, v in outcome.items()))
    if fatal:
        return EXIT_FATAL
    return EXIT_PARTIAL if "failed" in outcome.values() else EXIT_OK