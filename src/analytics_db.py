"""
SQLite storage for wars, Clan War League, Clan Capital and clan status history.

Only CREATE TABLE IF NOT EXISTS is used, so the existing snapshot tables
(snapshots, member_snapshots) are never touched.
"""

from contextlib import closing
from datetime import datetime, timezone

from src.database import USING_POSTGRES, dialect_schema, get_connection, insert_and_get_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS clan_meta (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at            TEXT NOT NULL,
    clan_tag            TEXT NOT NULL,
    war_league          TEXT,
    capital_league      TEXT,
    capital_hall_level  INTEGER,
    war_wins            INTEGER,
    war_ties            INTEGER,
    war_losses          INTEGER,
    war_win_streak      INTEGER,
    is_war_log_public   INTEGER,
    clan_points         INTEGER,
    capital_points      INTEGER
);

-- Detailed wars of our own clan: war_type is 'regular' or 'cwl'.
CREATE TABLE IF NOT EXISTS wars (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    war_key             TEXT NOT NULL UNIQUE,
    clan_tag            TEXT NOT NULL,
    war_type            TEXT NOT NULL,
    cwl_season          TEXT,
    cwl_round           INTEGER,
    war_tag             TEXT,
    state               TEXT,
    team_size           INTEGER,
    attacks_per_member  INTEGER,
    preparation_start   TEXT,
    start_time          TEXT,
    end_time            TEXT,
    opponent_tag        TEXT,
    opponent_name       TEXT,
    opponent_level      INTEGER,
    clan_stars          INTEGER,
    clan_destruction    REAL,
    clan_attacks        INTEGER,
    opp_stars           INTEGER,
    opp_destruction     REAL,
    opp_attacks         INTEGER,
    result              TEXT,
    first_seen_at       TEXT NOT NULL,
    last_updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS war_members (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    war_id            INTEGER NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    player_tag        TEXT NOT NULL,
    name              TEXT,
    town_hall         INTEGER,
    map_position      INTEGER,
    attacks_used      INTEGER,
    attack_limit      INTEGER,
    stars             INTEGER,
    destruction_total REAL,
    three_stars       INTEGER,
    opponent_attacks  INTEGER,
    best_opp_stars    INTEGER
);

CREATE TABLE IF NOT EXISTS war_attacks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    war_id            INTEGER NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    attacker_tag      TEXT,
    attacker_name     TEXT,
    attacker_th       INTEGER,
    attacker_position INTEGER,
    defender_tag      TEXT,
    defender_name     TEXT,
    defender_th       INTEGER,
    defender_position INTEGER,
    stars             INTEGER,
    destruction       REAL,
    attack_order      INTEGER,
    duration          INTEGER
);

-- Clan-level results from the API war log.
CREATE TABLE IF NOT EXISTS war_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_tag           TEXT NOT NULL,
    end_time           TEXT NOT NULL,
    result             TEXT,
    team_size          INTEGER,
    attacks_per_member INTEGER,
    opponent_tag       TEXT,
    opponent_name      TEXT,
    clan_stars         INTEGER,
    clan_destruction   REAL,
    clan_attacks       INTEGER,
    opp_stars          INTEGER,
    opp_destruction    REAL,
    exp_earned         INTEGER,
    first_seen_at      TEXT NOT NULL,
    UNIQUE (clan_tag, end_time)
);

CREATE TABLE IF NOT EXISTS cwl_seasons (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_tag         TEXT NOT NULL,
    season           TEXT NOT NULL,
    state            TEXT,
    war_league       TEXT,
    first_seen_at    TEXT NOT NULL,
    last_updated_at  TEXT NOT NULL,
    UNIQUE (clan_tag, season)
);

CREATE TABLE IF NOT EXISTS cwl_clans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   INTEGER NOT NULL REFERENCES cwl_seasons(id) ON DELETE CASCADE,
    clan_tag    TEXT NOT NULL,
    name        TEXT,
    clan_level  INTEGER,
    UNIQUE (season_id, clan_tag)
);

-- Our clan's registered CWL roster.
CREATE TABLE IF NOT EXISTS cwl_roster (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   INTEGER NOT NULL REFERENCES cwl_seasons(id) ON DELETE CASCADE,
    player_tag  TEXT NOT NULL,
    name        TEXT,
    town_hall   INTEGER,
    UNIQUE (season_id, player_tag)
);

-- Every war in the league group (all 8 clans), used for standings.
CREATE TABLE IF NOT EXISTS cwl_wars (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id         INTEGER NOT NULL REFERENCES cwl_seasons(id) ON DELETE CASCADE,
    round_no          INTEGER,
    war_tag           TEXT NOT NULL UNIQUE,
    state             TEXT,
    team_size         INTEGER,
    start_time        TEXT,
    end_time          TEXT,
    clan1_tag         TEXT,
    clan1_name        TEXT,
    clan1_stars       INTEGER,
    clan1_destruction REAL,
    clan1_attacks     INTEGER,
    clan2_tag         TEXT,
    clan2_name        TEXT,
    clan2_stars       INTEGER,
    clan2_destruction REAL,
    clan2_attacks     INTEGER,
    first_seen_at     TEXT NOT NULL,
    last_updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capital_raids (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    clan_tag                  TEXT NOT NULL,
    start_time                TEXT NOT NULL,
    end_time                  TEXT,
    state                     TEXT,
    capital_total_loot        INTEGER,
    raids_completed           INTEGER,
    total_attacks             INTEGER,
    enemy_districts_destroyed INTEGER,
    offensive_reward          INTEGER,
    defensive_reward          INTEGER,
    first_seen_at             TEXT NOT NULL,
    last_updated_at           TEXT NOT NULL,
    UNIQUE (clan_tag, start_time)
);

CREATE TABLE IF NOT EXISTS capital_raid_members (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    raid_id                 INTEGER NOT NULL REFERENCES capital_raids(id) ON DELETE CASCADE,
    player_tag              TEXT NOT NULL,
    name                    TEXT,
    attacks                 INTEGER,
    attack_limit            INTEGER,
    bonus_attack_limit      INTEGER,
    capital_resources_looted INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wars_clan ON wars (clan_tag, war_type, start_time);
CREATE INDEX IF NOT EXISTS idx_war_members_war ON war_members (war_id);
CREATE INDEX IF NOT EXISTS idx_war_members_player ON war_members (player_tag);
CREATE INDEX IF NOT EXISTS idx_war_attacks_war ON war_attacks (war_id);
CREATE INDEX IF NOT EXISTS idx_cwl_wars_season ON cwl_wars (season_id);
CREATE INDEX IF NOT EXISTS idx_raid_members_raid ON capital_raid_members (raid_id);
CREATE INDEX IF NOT EXISTS idx_raid_members_player ON capital_raid_members (player_tag);
"""


def init_analytics_db():
    """Create the Step 4 tables if missing. Safe to call on every start."""
    with closing(get_connection()) as conn:
        with conn:
            conn.executescript(dialect_schema(SCHEMA))


# ------------------------------------------------------------- helpers

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_coc_time(value):
    """'20260924T070000.000Z' -> '2026-09-24T07:00:00+00:00' (UTC), or None."""
    if not value:
        return None
    for pattern in ("%Y%m%dT%H%M%S.%fZ", "%Y%m%dT%H%M%SZ"):
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def _query(sql, params=()):
    with closing(get_connection()) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _upsert(conn, table, key, cols):
    """Insert or update the row identified by `key` (dict). Returns (id, created)."""
    now = _now()
    where = " AND ".join(f"{k} = ?" for k in key)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {where}", tuple(key.values())
    ).fetchone()
    if row:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(
            f"UPDATE {table} SET {sets}, last_updated_at = ? WHERE id = ?",
            (*cols.values(), now, row["id"]),
        )
        return row["id"], False

    data = {**key, **cols}
    names = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    new_id = insert_and_get_id(
        conn,
        f"INSERT INTO {table} ({names}, first_seen_at, last_updated_at) "
        f"VALUES ({marks}, ?, ?)",
        (*data.values(), now, now),
    )
    return new_id, True


# ------------------------------------------------------------ clan meta

def save_clan_meta(clan):
    """Store war record, war league and capital league from the clan profile."""
    capital = clan.get("clanCapital") or {}
    public = clan.get("isWarLogPublic")
    with closing(get_connection()) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO clan_meta (
                    taken_at, clan_tag, war_league, capital_league,
                    capital_hall_level, war_wins, war_ties, war_losses,
                    war_win_streak, is_war_log_public, clan_points, capital_points
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    clan.get("tag"),
                    (clan.get("warLeague") or {}).get("name"),
                    (clan.get("capitalLeague") or {}).get("name"),
                    capital.get("capitalHallLevel"),
                    clan.get("warWins"),
                    clan.get("warTies"),
                    clan.get("warLosses"),
                    clan.get("warWinStreak"),
                    None if public is None else int(bool(public)),
                    clan.get("clanPoints"),
                    clan.get("clanCapitalPoints"),
                ),
            )


def get_latest_meta(clan_tag):
    rows = _query(
        "SELECT * FROM clan_meta WHERE clan_tag = ? ORDER BY id DESC LIMIT 1",
        (clan_tag,),
    )
    return rows[0] if rows else None


# ----------------------------------------------------------------- wars

def _orient(war, clan_tag):
    """Return (our side, enemy side) of a war dict, or (None, None)."""
    a, b = war.get("clan") or {}, war.get("opponent") or {}
    if a.get("tag") == clan_tag:
        return a, b
    if b.get("tag") == clan_tag:
        return b, a
    return None, None


def _war_result(state, own, enemy):
    if state != "warEnded":
        return None
    mine = (own.get("stars") or 0, own.get("destructionPercentage") or 0)
    theirs = (enemy.get("stars") or 0, enemy.get("destructionPercentage") or 0)
    if mine > theirs:
        return "win"
    if mine < theirs:
        return "lose"
    return "tie"


def save_war(war, clan_tag, war_type="regular", cwl_season=None,
             cwl_round=None, war_tag=None):
    """
    Save (or update) one war of our clan with per-player rows and attacks.
    Returns the war id, or None if there is no war / our clan is not in it.
    """
    state = war.get("state")
    if not state or state == "notInWar":
        return None
    own, enemy = _orient(war, clan_tag)
    if own is None:
        return None

    start = parse_coc_time(war.get("startTime"))
    if war_type == "cwl":
        war_key = f"{clan_tag}|cwl|{war_tag}"
    else:
        war_key = f"{clan_tag}|{enemy.get('tag')}|{start}"

    limit = war.get("attacksPerMember") or (1 if war_type == "cwl" else 2)

    cols = {
        "clan_tag": clan_tag,
        "war_type": war_type,
        "cwl_season": cwl_season,
        "cwl_round": cwl_round,
        "war_tag": war_tag,
        "state": state,
        "team_size": war.get("teamSize"),
        "attacks_per_member": limit,
        "preparation_start": parse_coc_time(war.get("preparationStartTime")),
        "start_time": start,
        "end_time": parse_coc_time(war.get("endTime")),
        "opponent_tag": enemy.get("tag"),
        "opponent_name": enemy.get("name"),
        "opponent_level": enemy.get("clanLevel"),
        "clan_stars": own.get("stars"),
        "clan_destruction": own.get("destructionPercentage"),
        "clan_attacks": own.get("attacks"),
        "opp_stars": enemy.get("stars"),
        "opp_destruction": enemy.get("destructionPercentage"),
        "opp_attacks": enemy.get("attacks"),
        "result": _war_result(state, own, enemy),
    }

    enemy_members = {m.get("tag"): m for m in enemy.get("members", [])}
    member_rows, attack_rows = [], []

    def th_of(member):
        return member.get("townhallLevel") or member.get("townHallLevel")

    for m in own.get("members", []):
        attacks = m.get("attacks") or []
        best = m.get("bestOpponentAttack") or {}
        member_rows.append((
            m.get("tag"), m.get("name"), th_of(m), m.get("mapPosition"),
            len(attacks), limit,
            sum(a.get("stars") or 0 for a in attacks),
            sum(a.get("destructionPercentage") or 0 for a in attacks),
            sum(1 for a in attacks if a.get("stars") == 3),
            m.get("opponentAttacks"), best.get("stars"),
        ))
        for a in attacks:
            d = enemy_members.get(a.get("defenderTag")) or {}
            attack_rows.append((
                m.get("tag"), m.get("name"), th_of(m), m.get("mapPosition"),
                a.get("defenderTag"), d.get("name"), th_of(d), d.get("mapPosition"),
                a.get("stars"), a.get("destructionPercentage"),
                a.get("order"), a.get("duration"),
            ))

    with closing(get_connection()) as conn:
        with conn:
            war_id, _ = _upsert(conn, "wars", {"war_key": war_key}, cols)
            conn.execute("DELETE FROM war_members WHERE war_id = ?", (war_id,))
            conn.execute("DELETE FROM war_attacks WHERE war_id = ?", (war_id,))
            conn.executemany(
                """
                INSERT INTO war_members (
                    war_id, player_tag, name, town_hall, map_position,
                    attacks_used, attack_limit, stars, destruction_total,
                    three_stars, opponent_attacks, best_opp_stars
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(war_id, *row) for row in member_rows],
            )
            conn.executemany(
                """
                INSERT INTO war_attacks (
                    war_id, attacker_tag, attacker_name, attacker_th,
                    attacker_position, defender_tag, defender_name, defender_th,
                    defender_position, stars, destruction, attack_order, duration
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(war_id, *row) for row in attack_rows],
            )
    return war_id


def get_wars(clan_tag, war_type="regular"):
    """Saved detailed wars, newest first."""
    return _query(
        """
        SELECT * FROM wars WHERE clan_tag = ? AND war_type = ?
        ORDER BY COALESCE(start_time, first_seen_at) DESC, id DESC
        """,
        (clan_tag, war_type),
    )


def get_war_members(war_id):
    return _query(
        "SELECT * FROM war_members WHERE war_id = ? ORDER BY map_position", (war_id,)
    )


def get_war_attacks(war_id):
    return _query(
        "SELECT * FROM war_attacks WHERE war_id = ? ORDER BY attack_order", (war_id,)
    )


def get_war_member_rows(clan_tag, war_type, cwl_season=None):
    """Every player-in-war row (with war info) for analytics, oldest first."""
    sql = """
        SELECT w.id AS war_id, w.state, w.start_time, w.end_time,
               w.opponent_name, w.cwl_season, w.cwl_round, w.team_size, w.result,
               m.player_tag, m.name, m.town_hall, m.map_position,
               m.attacks_used, m.attack_limit, m.stars, m.destruction_total,
               m.three_stars, m.opponent_attacks, m.best_opp_stars
        FROM war_members m JOIN wars w ON w.id = m.war_id
        WHERE w.clan_tag = ? AND w.war_type = ?
    """
    params = [clan_tag, war_type]
    if cwl_season is not None:
        sql += " AND w.cwl_season = ?"
        params.append(cwl_season)
    sql += " ORDER BY COALESCE(w.start_time, w.first_seen_at), m.map_position"
    return _query(sql, tuple(params))


def save_war_log(clan_tag, items):
    """Store API war-log entries. Returns how many were new."""
    new = 0
    with closing(get_connection()) as conn:
        with conn:
            for item in items:
                end = parse_coc_time(item.get("endTime"))
                if not end:
                    continue
                clan = item.get("clan") or {}
                opp = item.get("opponent") or {}
                insert_sql = (
                    """
                    INSERT INTO war_log (
                        clan_tag, end_time, result, team_size, attacks_per_member,
                        opponent_tag, opponent_name, clan_stars, clan_destruction,
                        clan_attacks, opp_stars, opp_destruction, exp_earned,
                        first_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (clan_tag, end_time) DO NOTHING
                    """
                    if USING_POSTGRES else
                    """
                    INSERT OR IGNORE INTO war_log (
                        clan_tag, end_time, result, team_size, attacks_per_member,
                        opponent_tag, opponent_name, clan_stars, clan_destruction,
                        clan_attacks, opp_stars, opp_destruction, exp_earned,
                        first_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                cursor = conn.execute(
                    insert_sql,
                    (
                        clan_tag, end, item.get("result"), item.get("teamSize"),
                        item.get("attacksPerMember"), opp.get("tag"), opp.get("name"),
                        clan.get("stars"), clan.get("destructionPercentage"),
                        clan.get("attacks"), opp.get("stars"),
                        opp.get("destructionPercentage"), clan.get("expEarned"),
                        _now(),
                    ),
                )
                new += cursor.rowcount
    return new


def get_war_log(clan_tag):
    return _query(
        "SELECT * FROM war_log WHERE clan_tag = ? ORDER BY end_time DESC", (clan_tag,)
    )


# ------------------------------------------------------------------ CWL

def save_cwl_group(group, clan_tag, war_league=None):
    """Store a CWL league group (season, clans, our roster). Returns the season id."""
    season = group.get("season")
    if not season:
        return None

    cols = {"state": group.get("state")}
    if war_league:
        cols["war_league"] = war_league

    with closing(get_connection()) as conn:
        with conn:
            season_id, _ = _upsert(
                conn, "cwl_seasons", {"clan_tag": clan_tag, "season": season}, cols
            )
            conn.execute("DELETE FROM cwl_clans WHERE season_id = ?", (season_id,))
            conn.execute("DELETE FROM cwl_roster WHERE season_id = ?", (season_id,))
            clans_sql = (
                "INSERT INTO cwl_clans (season_id, clan_tag, name, clan_level) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (season_id, clan_tag) DO NOTHING"
                if USING_POSTGRES else
                "INSERT OR IGNORE INTO cwl_clans (season_id, clan_tag, name, clan_level) "
                "VALUES (?, ?, ?, ?)"
            )
            roster_sql = (
                "INSERT INTO cwl_roster (season_id, player_tag, name, town_hall) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (season_id, player_tag) DO NOTHING"
                if USING_POSTGRES else
                "INSERT OR IGNORE INTO cwl_roster (season_id, player_tag, name, town_hall) "
                "VALUES (?, ?, ?, ?)"
            )
            for c in group.get("clans", []):
                conn.execute(
                    clans_sql,
                    (season_id, c.get("tag"), c.get("name"), c.get("clanLevel")),
                )
                if c.get("tag") == clan_tag:
                    conn.executemany(
                        roster_sql,
                        [
                            (season_id, m.get("tag"), m.get("name"), m.get("townHallLevel"))
                            for m in c.get("members", [])
                        ],
                    )
    return season_id


def save_cwl_war(season_id, season, round_no, war_tag, war, clan_tag):
    """Store one CWL war of the group; also store details if our clan played it."""
    a, b = war.get("clan") or {}, war.get("opponent") or {}
    cols = {
        "season_id": season_id,
        "round_no": round_no,
        "state": war.get("state"),
        "team_size": war.get("teamSize"),
        "start_time": parse_coc_time(war.get("startTime")),
        "end_time": parse_coc_time(war.get("endTime")),
        "clan1_tag": a.get("tag"),
        "clan1_name": a.get("name"),
        "clan1_stars": a.get("stars"),
        "clan1_destruction": a.get("destructionPercentage"),
        "clan1_attacks": a.get("attacks"),
        "clan2_tag": b.get("tag"),
        "clan2_name": b.get("name"),
        "clan2_stars": b.get("stars"),
        "clan2_destruction": b.get("destructionPercentage"),
        "clan2_attacks": b.get("attacks"),
    }
    with closing(get_connection()) as conn:
        with conn:
            _upsert(conn, "cwl_wars", {"war_tag": war_tag}, cols)

    save_war(war, clan_tag, war_type="cwl", cwl_season=season,
             cwl_round=round_no, war_tag=war_tag)


def get_finished_cwl_war_tags():
    """War tags already stored in their final state (no need to fetch again)."""
    rows = _query("SELECT war_tag FROM cwl_wars WHERE state = 'warEnded'")
    return {r["war_tag"] for r in rows}


def get_cwl_seasons(clan_tag):
    return _query(
        "SELECT * FROM cwl_seasons WHERE clan_tag = ? ORDER BY season DESC", (clan_tag,)
    )


def get_cwl_clans(season_id):
    return _query("SELECT * FROM cwl_clans WHERE season_id = ?", (season_id,))


def get_cwl_roster(season_id):
    return _query(
        "SELECT * FROM cwl_roster WHERE season_id = ? ORDER BY town_hall DESC, name",
        (season_id,),
    )


def get_cwl_wars(season_id):
    return _query(
        "SELECT * FROM cwl_wars WHERE season_id = ? ORDER BY round_no, id", (season_id,)
    )


# -------------------------------------------------------- Clan Capital

def save_capital_raids(clan_tag, items):
    """Store raid weekends. Returns (entries read, entries that were new)."""
    created = 0
    with closing(get_connection()) as conn:
        with conn:
            for item in items:
                start = parse_coc_time(item.get("startTime"))
                if not start:
                    continue
                cols = {
                    "end_time": parse_coc_time(item.get("endTime")),
                    "state": item.get("state"),
                    "capital_total_loot": item.get("capitalTotalLoot"),
                    "raids_completed": item.get("raidsCompleted"),
                    "total_attacks": item.get("totalAttacks"),
                    "enemy_districts_destroyed": item.get("enemyDistrictsDestroyed"),
                    "offensive_reward": item.get("offensiveReward"),
                    "defensive_reward": item.get("defensiveReward"),
                }
                raid_id, is_new = _upsert(
                    conn, "capital_raids",
                    {"clan_tag": clan_tag, "start_time": start}, cols,
                )
                created += int(is_new)
                conn.execute(
                    "DELETE FROM capital_raid_members WHERE raid_id = ?", (raid_id,)
                )
                conn.executemany(
                    """
                    INSERT INTO capital_raid_members (
                        raid_id, player_tag, name, attacks, attack_limit,
                        bonus_attack_limit, capital_resources_looted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            raid_id, m.get("tag"), m.get("name"), m.get("attacks"),
                            m.get("attackLimit"), m.get("bonusAttackLimit"),
                            m.get("capitalResourcesLooted"),
                        )
                        for m in item.get("members", [])
                    ],
                )
    return len(items), created


def get_capital_raids(clan_tag):
    return _query(
        """
        SELECT r.*,
               (SELECT COUNT(*) FROM capital_raid_members m
                WHERE m.raid_id = r.id) AS participants
        FROM capital_raids r WHERE r.clan_tag = ?
        ORDER BY r.start_time DESC
        """,
        (clan_tag,),
    )


def get_capital_members(raid_id):
    return _query(
        "SELECT * FROM capital_raid_members WHERE raid_id = ? "
        "ORDER BY capital_resources_looted DESC",
        (raid_id,),
    )


def get_capital_member_rows(clan_tag):
    return _query(
        """
        SELECT r.id AS raid_id, r.start_time, r.end_time, r.state,
               m.player_tag, m.name, m.attacks, m.attack_limit,
               m.bonus_attack_limit, m.capital_resources_looted
        FROM capital_raid_members m JOIN capital_raids r ON r.id = m.raid_id
        WHERE r.clan_tag = ?
        ORDER BY r.start_time
        """,
        (clan_tag,),
    )