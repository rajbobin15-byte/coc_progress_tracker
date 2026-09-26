"""CWL page: league group, standings, rounds and player performance."""

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics_db import (
    get_cwl_clans,
    get_cwl_roster,
    get_cwl_seasons,
    get_cwl_wars,
    get_finished_cwl_war_tags,
    get_war_member_rows,
    save_clan_meta,
    save_cwl_group,
    save_cwl_war,
)
from src.analytics_ui import (
    STATE_LABELS,
    fetch_button,
    int_cols,
    num_cols,
    show_raw,
    when,
)
from src.coc_api import (
    ClashAPIError,
    ManualTokenRequired,
    NotFoundError,
    WarLogPrivateError,
    get_clan_info,
    get_cwl_group,
    get_cwl_war,
)
from src.ui_helpers import current_clan_tag, fmt

GROUP_STATES = {
    "preparation": "Preparation",
    "inWar": "In progress",
    "ended": "Ended",
    "notInWar": "Not in war",
}


# ---------------------------------------------------------------- fetch

def _fetch(clan_tag):
    results = []
    clan = get_clan_info()
    save_clan_meta(clan)
    league = (clan.get("warLeague") or {}).get("name")

    try:
        group = get_cwl_group()
    except WarLogPrivateError as exc:
        return [("warning", f"**{exc.title}**\n\n{exc}")]
    except ManualTokenRequired as exc:
        return [("manual_token", exc)]
    except ClashAPIError as exc:
        return [("error", f"**{exc.title}**\n\n{exc}")]

    if group is None:
        return [(
            "info",
            "Your clan is not in a Clan War League group right now, so the API has "
            "no CWL data to return. CWL usually runs during roughly the first 10 days "
            "of each month. Seasons you saved earlier are shown below.",
        )]

    st.session_state["cwl_raw"] = group
    season = group.get("season")
    season_id = save_cwl_group(group, clan_tag, league)
    if season_id is None:
        return [("warning", "The API returned a league group without a season, so nothing was saved.")]

    known_final = get_finished_cwl_war_tags()
    todo = []
    for round_no, rnd in enumerate(group.get("rounds", []), start=1):
        for tag in rnd.get("warTags", []):
            if tag and tag != "#0":  # '#0' = round not decided yet
                todo.append((round_no, tag))
    to_fetch = [item for item in todo if item[1] not in known_final]

    saved = 0
    if to_fetch:
        progress = st.progress(0.0, text="Fetching CWL wars...")
        for i, (round_no, tag) in enumerate(to_fetch, start=1):
            try:
                war = get_cwl_war(tag)
            except NotFoundError:
                continue
            except ManualTokenRequired as exc:
                results.append(("manual_token", exc))
                break
            except ClashAPIError as exc:
                results.append((
                    "error",
                    f"Stopped after {saved} of {len(to_fetch)} wars.\n\n"
                    f"**{exc.title}**\n\n{exc}\n\nEverything fetched before the error is saved.",
                ))
                break
            save_cwl_war(season_id, season, round_no, tag, war, clan_tag)
            saved += 1
            progress.progress(i / len(to_fetch), text=f"Fetched {i} of {len(to_fetch)} wars")
        progress.empty()

    results.insert(0, (
        "success",
        f"CWL season {season} ({GROUP_STATES.get(group.get('state'), group.get('state'))}): "
        f"{len(group.get('clans', []))} clans, {len(todo)} wars known so far, "
        f"{saved} fetched or updated, {len(todo) - len(to_fetch)} already final and skipped.",
    ))
    return results


# ------------------------------------------------------------- standings

def _standings(clans, wars, clan_tag):
    stats = {
        c["clan_tag"]: {
            "Clan": c["name"], "Tag": c["clan_tag"], "Rounds": 0, "Wins": 0,
            "Ties": 0, "Losses": 0, "Stars": 0, "Destruction": 0.0,
        }
        for c in clans
    }
    for w in wars:
        if w["state"] != "warEnded":
            continue
        for me, other in (("1", "2"), ("2", "1")):
            tag = w[f"clan{me}_tag"]
            if tag not in stats:
                continue
            mine = (w[f"clan{me}_stars"] or 0, w[f"clan{me}_destruction"] or 0.0)
            theirs = (w[f"clan{other}_stars"] or 0, w[f"clan{other}_destruction"] or 0.0)
            row = stats[tag]
            row["Rounds"] += 1
            row["Stars"] += mine[0]
            row["Destruction"] += mine[1]
            if mine > theirs:
                row["Wins"] += 1
            elif mine == theirs:
                row["Ties"] += 1
            else:
                row["Losses"] += 1

    df = pd.DataFrame(stats.values())
    if df.empty:
        return df
    df["Points"] = df["Stars"] + 10 * df["Wins"]
    df = df.sort_values(["Points", "Destruction"], ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)
    df["Destruction"] = df["Destruction"].round(1)
    df["Clan"] = [
        f"{name} (your clan)" if tag == clan_tag else name
        for name, tag in zip(df["Clan"], df["Tag"])
    ]
    return df.rename(columns={"Destruction": "Total destruction %"})


def _render_standings(clans, wars, clan_tag):
    table = _standings(clans, wars, clan_tag)
    if table.empty or table["Rounds"].sum() == 0:
        st.info("No CWL round has finished yet, so there are no standings.")
        return
    st.dataframe(table, hide_index=True)
    st.caption(
        "Computed from finished rounds only. Points = stars + 10 per war win; ties "
        "are broken by total destruction. The API does not provide official "
        "standings, so this may differ slightly from the in-game table."
    )
    fig = px.bar(
        table, x="Clan", y=["Stars", "Points"], barmode="group",
        title="Stars and points per clan (finished rounds)",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=table["Clan"].tolist())
    st.plotly_chart(fig)


# ---------------------------------------------------------------- rounds

def _render_rounds(wars, clan_tag):
    if not wars:
        st.info("No CWL wars saved for this season yet.")
        return
    only_ours = st.checkbox("Only your clan's wars", value=True, key="cwl_only_ours")
    rows = []
    for w in wars:
        if only_ours and clan_tag not in (w["clan1_tag"], w["clan2_tag"]):
            continue
        ended = w["state"] == "warEnded"
        a = (w["clan1_stars"] or 0, w["clan1_destruction"] or 0.0)
        b = (w["clan2_stars"] or 0, w["clan2_destruction"] or 0.0)
        winner = ""
        if ended:
            winner = w["clan1_name"] if a > b else w["clan2_name"] if b > a else "Tie"
        rows.append({
            "Round": w["round_no"],
            "State": STATE_LABELS.get(w["state"], w["state"]),
            "Clan 1": w["clan1_name"],
            "Stars 1": w["clan1_stars"],
            "Destruction 1 %": round(w["clan1_destruction"], 1) if w["clan1_destruction"] is not None else None,
            "Clan 2": w["clan2_name"],
            "Stars 2": w["clan2_stars"],
            "Destruction 2 %": round(w["clan2_destruction"], 1) if w["clan2_destruction"] is not None else None,
            "Winner": winner,
            "Ends": when(w["end_time"]),
        })
    if not rows:
        st.info("Your clan has no saved wars in this season yet.")
        return
    table = pd.DataFrame(rows)
    int_cols(table, ["Round", "Stars 1", "Stars 2"])
    st.dataframe(table, hide_index=True)


# --------------------------------------------------------------- players

def _render_players(clan_tag, season):
    rows = get_war_member_rows(clan_tag, "cwl", cwl_season=season)
    if not rows:
        st.info("No player war data saved for this season yet.")
        return
    df = pd.DataFrame(rows)
    num_cols(df, [
        "cwl_round", "town_hall", "attacks_used", "attack_limit", "stars",
        "destruction_total", "three_stars",
    ])
    df["ended"] = df["state"] == "warEnded"
    df["missed"] = (df["attack_limit"] - df["attacks_used"]).clip(lower=0).where(df["ended"])
    df["Round"] = "R" + df["cwl_round"].astype(int).astype(str)

    g = df.groupby(["player_tag", "name"], as_index=False).agg(
        town_hall=("town_hall", "max"),
        rounds=("war_id", "nunique"),
        attacks=("attacks_used", "sum"),
        missed=("missed", "sum"),
        stars=("stars", "sum"),
        three=("three_stars", "sum"),
        destruction=("destruction_total", "sum"),
    )
    used = g["attacks"].where(g["attacks"] > 0)
    table = pd.DataFrame({
        "Name": g["name"],
        "Tag": g["player_tag"],
        "Town Hall": g["town_hall"],
        "Rounds in lineup": g["rounds"],
        "Attacks used": g["attacks"],
        "Missed attacks": g["missed"],
        "Stars": g["stars"],
        "3-star attacks": g["three"],
        "Avg stars / attack": (g["stars"] / used).round(2),
        "Avg destruction %": (g["destruction"] / used).round(1),
    }).sort_values(["Stars", "Avg destruction %"], ascending=False)
    int_cols(table, ["Town Hall", "Rounds in lineup", "Attacks used",
                     "Missed attacks", "Stars", "3-star attacks"])
    st.dataframe(table, hide_index=True)
    st.caption("Missed attacks only count rounds that have finished.")

    order = table["Name"].tolist()
    fig = px.bar(
        df, x="name", y="stars", color="Round", barmode="stack",
        labels={"name": "Player", "stars": "Stars"},
        title="Stars per player, split by round",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=order)
    st.plotly_chart(fig)

    st.subheader("Stars per round")
    pivot = df.pivot_table(index="name", columns="cwl_round", values="stars", aggfunc="sum")
    pivot.columns = [f"R{int(c)}" for c in pivot.columns]
    pivot = pivot.reindex(order).astype("Int64").reset_index().rename(columns={"name": "Name"})
    st.dataframe(pivot, hide_index=True)
    st.caption("Blank = the player was not in that round's lineup.")


def _render_roster(season_id, clan_tag, season):
    roster = get_cwl_roster(season_id)
    if not roster:
        st.info("No roster saved for this season.")
        return
    played = {}
    for row in get_war_member_rows(clan_tag, "cwl", cwl_season=season):
        entry = played.setdefault(row["player_tag"], {"rounds": 0, "attacks": 0})
        entry["rounds"] += 1
        entry["attacks"] += row["attacks_used"] or 0
    table = pd.DataFrame({
        "Name": [r["name"] for r in roster],
        "Tag": [r["player_tag"] for r in roster],
        "Town Hall": [r["town_hall"] for r in roster],
        "Rounds in lineup": [played.get(r["player_tag"], {}).get("rounds", 0) for r in roster],
        "Attacks used": [played.get(r["player_tag"], {}).get("attacks", 0) for r in roster],
    })
    st.dataframe(table, hide_index=True)
    st.caption(
        "The registered roster is usually larger than the 15 players used per round. "
        "0 rounds = registered but not (yet) in any lineup."
    )


# ------------------------------------------------------------------ page

def render_cwl():
    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    st.caption(
        "Each click fetches the league group plus every CWL war that is not final "
        "yet (up to about 30 API requests on the first click of a season, then only "
        "the new ones). Click once per day during CWL."
    )
    fetch_button("Fetch & Save CWL Data", "cwl_fetch", lambda: _fetch(clan_tag))

    seasons = get_cwl_seasons(clan_tag)
    if not seasons:
        st.info(
            "No CWL data saved yet. CWL only exists for a few days each month "
            "(roughly the 1st to 10th). Click **Fetch & Save CWL Data** while your "
            "clan is in a CWL group."
        )
        show_raw("cwl_raw")
        return

    by_id = {s["id"]: s for s in seasons}
    season_id = st.selectbox(
        "Season", list(by_id), key="cwl_season",
        format_func=lambda i: f"{by_id[i]['season']} - "
                              f"{GROUP_STATES.get(by_id[i]['state'], by_id[i]['state'])}",
    )
    s = by_id[season_id]
    clans = get_cwl_clans(season_id)
    wars = get_cwl_wars(season_id)

    c = st.columns(5)
    c[0].metric("Season", s["season"])
    c[1].metric("Season state", GROUP_STATES.get(s["state"], s["state"] or "N/A"))
    c[2].metric("Your CWL league", s["war_league"] or "N/A")
    c[3].metric("Clans in group", fmt(len(clans)))
    c[4].metric("Last updated", when(s["last_updated_at"]))
    st.caption("The league is taken from your clan profile at the time of the fetch.")

    tabs = st.tabs(["Standings", "Rounds", "Player performance", "Roster"])
    with tabs[0]:
        _render_standings(clans, wars, clan_tag)
    with tabs[1]:
        _render_rounds(wars, clan_tag)
    with tabs[2]:
        _render_players(clan_tag, s["season"])
    with tabs[3]:
        _render_roster(season_id, clan_tag, s["season"])

    show_raw("cwl_raw")