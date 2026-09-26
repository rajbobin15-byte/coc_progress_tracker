"""
SQLite/Postgres storage for individual player profile snapshots.

Separate from the clan roster snapshots in database.py: this stores the full
player profile (heroes, troops, spells, pets, hero equipment, achievements)
for ANY player tag looked up on the Player Profile page, not just current
clan members. Uses the same get_connection()/dialect helpers as the rest of
the app, so it works on SQLite locally and Postgres (e.g. Neon) in production.
"""

import json
from contextlib import closing
from datetime import datetime, timezone

from src.database import dialect_schema, get_connection, insert_and_get_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS player_snapshots (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at                    TEXT NOT NULL,
    player_tag                  TEXT NOT NULL,
    name                        TEXT,
    town_hall_level             INTEGER,
    town_hall_weapon_level      INTEGER,
    exp_level                   INTEGER,
    trophies                    INTEGER,
    best_trophies               INTEGER,
    war_stars                   INTEGER,
    attack_wins                 INTEGER,
    defense_wins                INTEGER,
    builder_hall_level          INTEGER,
    builder_base_trophies       INTEGER,
    best_builder_base_trophies  INTEGER,
    role                        TEXT,
    donations                   INTEGER,
    donations_received          INTEGER,
    clan_capital_contributions  INTEGER,
    clan_tag                    TEXT,
    clan_name                   TEXT,
    league_name                 TEXT,
    builder_league_name         TEXT,
    heroes_json                 TEXT,
    hero_equipment_json         TEXT,
    troops_json                 TEXT,
    spells_json                 TEXT,
    pets_json                   TEXT,
    achievements_json           TEXT
);

CREATE INDEX IF NOT EXISTS idx_player_snapshots_tag
    ON player_snapshots (player_tag, taken_at);
"""


def init_player_db():
    """Create the player_snapshots table if missing. Safe to call repeatedly."""
    with closing(get_connection()) as conn:
        with conn:
            conn.executescript(dialect_schema(SCHEMA))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _name_of(value):
    return value.get("name") if isinstance(value, dict) else None


def _dump(items):
    return json.dumps(items or [])


def save_player_snapshot(player):
    """Store one player profile snapshot (the dict from get_player_info). Returns its id."""
    clan = player.get("clan") or {}
    taken_at = _now()

    with closing(get_connection()) as conn:
        with conn:
            new_id = insert_and_get_id(
                conn,
                """
                INSERT INTO player_snapshots (
                    taken_at, player_tag, name, town_hall_level,
                    town_hall_weapon_level, exp_level, trophies, best_trophies,
                    war_stars, attack_wins, defense_wins, builder_hall_level,
                    builder_base_trophies, best_builder_base_trophies, role,
                    donations, donations_received, clan_capital_contributions,
                    clan_tag, clan_name, league_name, builder_league_name,
                    heroes_json, hero_equipment_json, troops_json, spells_json,
                    pets_json, achievements_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    taken_at,
                    player.get("tag"),
                    player.get("name"),
                    player.get("townHallLevel"),
                    player.get("townHallWeaponLevel"),
                    player.get("expLevel"),
                    player.get("trophies"),
                    player.get("bestTrophies"),
                    player.get("warStars"),
                    player.get("attackWins"),
                    player.get("defenseWins"),
                    player.get("builderHallLevel"),
                    player.get("builderBaseTrophies"),
                    player.get("bestBuilderBaseTrophies"),
                    player.get("role"),
                    player.get("donations"),
                    player.get("donationsReceived"),
                    player.get("clanCapitalContributions"),
                    clan.get("tag"),
                    clan.get("name"),
                    _name_of(player.get("league")),
                    _name_of(player.get("builderBaseLeague")),
                    _dump(player.get("heroes")),
                    _dump(player.get("heroEquipment")),
                    _dump(player.get("troops")),
                    _dump(player.get("spells")),
                    _dump(player.get("pets")),
                    _dump(player.get("achievements")),
                ),
            )
    return new_id


def _row_with_json(row):
    if row is None:
        return None
    row = dict(row)
    for field in (
        "heroes_json", "hero_equipment_json", "troops_json",
        "spells_json", "pets_json", "achievements_json",
    ):
        row[field.replace("_json", "")] = json.loads(row.pop(field) or "[]")
    return row


def get_latest_player_snapshot(player_tag):
    """Return the newest snapshot for this player as a dict, or None."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM player_snapshots WHERE player_tag = ? "
            "ORDER BY id DESC LIMIT 1",
            (player_tag,),
        ).fetchone()
    return _row_with_json(row)


def get_player_snapshot_count(player_tag):
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM player_snapshots WHERE player_tag = ?",
            (player_tag,),
        ).fetchone()
    return row["n"]


def get_player_history(player_tag):
    """
    Every stored snapshot's scalar (non-JSON) fields for this player, oldest
    first - enough to chart trophies/war stars/donations over time.
    """
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT id, taken_at, name, town_hall_level, exp_level, trophies,
                   best_trophies, war_stars, attack_wins, defense_wins,
                   builder_hall_level, builder_base_trophies,
                   best_builder_base_trophies, donations, donations_received,
                   clan_capital_contributions, league_name
            FROM player_snapshots
            WHERE player_tag = ?
            ORDER BY id
            """,
            (player_tag,),
        ).fetchall()
    return [dict(r) for r in rows]