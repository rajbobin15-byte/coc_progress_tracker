"""
Storage for clan snapshots - SQLite locally, Postgres in production.

Each time you save a snapshot, one row goes into `snapshots` (clan-level data)
and one row per member goes into `member_snapshots`. Old snapshots are never
overwritten, which is what makes history and trends possible.

Dialect switch: if the DATABASE_URL environment variable is set (e.g. a Neon
or Render Postgres connection string), every connection goes to Postgres and
survives redeploys. If it is not set, everything falls back to the original
local SQLite file at data/clash.db - nothing changes for local development.
All the query functions below (and in analytics_db.py) use '?' placeholders
and dict-like rows either way; PGConnection translates that automatically.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "clash.db"

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USING_POSTGRES = bool(DATABASE_URL)

if USING_POSTGRES:
    import psycopg2
    import psycopg2.extras


class PGConnection:
    """
    Thin wrapper so a psycopg2 connection can be used exactly like the
    sqlite3 connections the rest of this codebase was written against:
    conn.execute(sql, params), conn.executemany(...), conn.executescript(...),
    dict-like rows, and `with conn:` for commit/rollback.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn

    @staticmethod
    def _adapt(sql):
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(self._adapt(sql), tuple(params))
        return cur

    def executemany(self, sql, seq_of_params):
        cur = self._conn.cursor()
        cur.executemany(self._adapt(sql), list(seq_of_params))
        return cur

    def executescript(self, script):
        # No bound parameters, so psycopg2 can send several ';'-separated
        # statements in one call - this is only used for CREATE TABLE DDL.
        cur = self._conn.cursor()
        cur.execute(script)
        return cur

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def close(self):
        self._conn.close()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


def insert_and_get_id(conn, sql, params):
    """
    Run an INSERT and return the new row's id, on either engine.
    sqlite3 exposes cursor.lastrowid; Postgres needs 'RETURNING id' instead.
    """
    if USING_POSTGRES and "returning" not in sql.lower():
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
    cursor = conn.execute(sql, params)
    if USING_POSTGRES:
        return cursor.fetchone()["id"]
    return cursor.lastrowid


def dialect_schema(schema_sql):
    """Rewrite SQLite-only DDL syntax for Postgres. Safe to call either way."""
    if USING_POSTGRES:
        return schema_sql.replace(
            "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
        )
    return schema_sql

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at            TEXT    NOT NULL,   -- UTC, ISO 8601
    clan_tag            TEXT    NOT NULL,
    clan_name           TEXT,
    clan_level          INTEGER,
    clan_points         INTEGER,
    builder_base_points INTEGER,
    capital_points      INTEGER,
    member_count        INTEGER
);

CREATE TABLE IF NOT EXISTS member_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id           INTEGER NOT NULL
                          REFERENCES snapshots(id) ON DELETE CASCADE,
    player_tag            TEXT    NOT NULL,
    name                  TEXT,
    role                  TEXT,
    town_hall_level       INTEGER,
    exp_level             INTEGER,
    trophies              INTEGER,
    builder_base_trophies INTEGER,
    clan_rank             INTEGER,
    previous_clan_rank    INTEGER,
    donations             INTEGER,
    donations_received    INTEGER,
    league_name           TEXT,
    builder_league_name   TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_clan
    ON snapshots (clan_tag, taken_at);
CREATE INDEX IF NOT EXISTS idx_member_snapshot
    ON member_snapshots (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_member_player
    ON member_snapshots (player_tag);
"""

# Columns added after the first version of the schema. Databases created by
# the earlier Step 3 code get these added automatically by _migrate().
MEMBER_MIGRATIONS = {
    "league_name": "TEXT",
    "builder_league_name": "TEXT",
}


def get_connection():
    if USING_POSTGRES:
        return PGConnection(psycopg2.connect(DATABASE_URL))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn):
    if USING_POSTGRES:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'member_snapshots'"
        ).fetchall()
        existing = {row["column_name"] for row in rows}
    else:
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(member_snapshots)")
        }
    for column, column_type in MEMBER_MIGRATIONS.items():
        if column not in existing:
            conn.execute(
                f"ALTER TABLE member_snapshots ADD COLUMN {column} {column_type}"
            )


def init_db():
    """Create tables and apply small upgrades. Safe to call repeatedly."""
    with closing(get_connection()) as conn:
        with conn:
            conn.executescript(dialect_schema(SCHEMA))
            _migrate(conn)


def _name_of(value):
    """League fields are objects like {'id': ..., 'name': ...}."""
    return value.get("name") if isinstance(value, dict) else None


def save_snapshot(clan):
    """Store one clan snapshot (the dict from get_clan_info). Returns its id."""
    taken_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with closing(get_connection()) as conn:
        with conn:  # one transaction: all rows are saved, or none
            snapshot_id = insert_and_get_id(
                conn,
                """
                INSERT INTO snapshots (
                    taken_at, clan_tag, clan_name, clan_level, clan_points,
                    builder_base_points, capital_points, member_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    taken_at,
                    clan.get("tag"),
                    clan.get("name"),
                    clan.get("clanLevel"),
                    clan.get("clanPoints"),
                    clan.get("clanBuilderBasePoints"),
                    clan.get("clanCapitalPoints"),
                    clan.get("members"),
                ),
            )

            conn.executemany(
                """
                INSERT INTO member_snapshots (
                    snapshot_id, player_tag, name, role, town_hall_level,
                    exp_level, trophies, builder_base_trophies, clan_rank,
                    previous_clan_rank, donations, donations_received,
                    league_name, builder_league_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        m.get("tag"),
                        m.get("name"),
                        m.get("role"),
                        m.get("townHallLevel"),
                        m.get("expLevel"),
                        m.get("trophies"),
                        m.get("builderBaseTrophies"),
                        m.get("clanRank"),
                        m.get("previousClanRank"),
                        m.get("donations"),
                        m.get("donationsReceived"),
                        # Some API versions return `leagueTier`; fall back to `league`.
                        _name_of(m.get("leagueTier")) or _name_of(m.get("league")),
                        _name_of(m.get("builderBaseLeague")),
                    )
                    for m in clan.get("memberList", [])
                ],
            )
    return snapshot_id


def get_snapshot_count(clan_tag):
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM snapshots WHERE clan_tag = ?", (clan_tag,)
        ).fetchone()
    return row["n"]


def get_latest_snapshot(clan_tag):
    """Return the newest snapshot row as a dict, or None if there is none."""
    with closing(get_connection()) as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE clan_tag = ? ORDER BY id DESC LIMIT 1",
            (clan_tag,),
        ).fetchone()
    return dict(row) if row else None


def get_snapshot_list(clan_tag):
    """All snapshots for this clan, newest first."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE clan_tag = ? ORDER BY id DESC",
            (clan_tag,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_snapshot_members(snapshot_id):
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM member_snapshots
            WHERE snapshot_id = ?
            ORDER BY clan_rank
            """,
            (snapshot_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_member_history(clan_tag):
    """Every stored member row for this clan, oldest snapshot first."""
    with closing(get_connection()) as conn:
        rows = conn.execute(
            """
            SELECT s.id AS snapshot_id, s.taken_at,
                   m.player_tag, m.name, m.role, m.town_hall_level,
                   m.exp_level, m.trophies, m.builder_base_trophies,
                   m.clan_rank, m.donations, m.donations_received
            FROM member_snapshots m
            JOIN snapshots s ON s.id = m.snapshot_id
            WHERE s.clan_tag = ?
            ORDER BY s.id, m.clan_rank
            """,
            (clan_tag,),
        ).fetchall()
    return [dict(r) for r in rows]