"""Clan Capital page: league, raid weekends, loot and participation."""

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics_db import (
    get_capital_members,
    get_capital_raids,
    get_latest_meta,
    save_capital_raids,
    save_clan_meta,
)
from src.analytics_ui import STATE_LABELS, fetch_button, int_cols, num_cols, show_raw, when
from src.coc_api import get_capital_raid_seasons, get_clan_info
from src.database import get_latest_snapshot, get_snapshot_members
from src.ui_helpers import ROLE_LABELS, current_clan_tag, fmt


def _fetch(clan_tag):
    clan = get_clan_info()
    save_clan_meta(clan)
    items = get_capital_raid_seasons(limit=20)
    st.session_state["capital_raw"] = items[:1]
    if not items:
        return [("info", "The API returned no raid weekends for this clan.")]
    total, new = save_capital_raids(clan_tag, items)
    return [(
        "success",
        f"Clan profile saved. Raid weekends: {total} read from the API, {new} new "
        f"({total - new} existing ones were updated).",
    )]


def _status(clan_tag):
    meta = get_latest_meta(clan_tag)
    if not meta:
        st.caption("Click **Fetch & Save Capital Data** to load the Capital league.")
        return
    c = st.columns(3)
    c[0].metric("Capital league", meta["capital_league"] or "N/A")
    c[1].metric("Capital Hall level", fmt(meta["capital_hall_level"]))
    c[2].metric("Clan Capital points", fmt(meta["capital_points"]))
    st.caption(f"As of {when(meta['taken_at'])}.")


def _raid_label(r):
    return (
        f"{when(r['start_time'])[:10]} to {when(r['end_time'])[:10]} - "
        f"{STATE_LABELS.get(r['state'], r['state'])}"
    )


def _render_weekend(clan_tag, raids):
    by_id = {r["id"]: r for r in raids}
    raid_id = st.selectbox(
        "Raid weekend (newest first)", list(by_id),
        format_func=lambda i: _raid_label(by_id[i]), key="capital_selected",
    )
    r = by_id[raid_id]
    ended = r["state"] == "ended"

    c = st.columns(4)
    c[0].metric("Total capital gold looted", fmt(r["capital_total_loot"]))
    c[1].metric("Raids completed", fmt(r["raids_completed"]))
    c[2].metric("Total attacks", fmt(r["total_attacks"]))
    c[3].metric("Enemy districts destroyed", fmt(r["enemy_districts_destroyed"]))
    c = st.columns(3)
    c[0].metric("Offensive reward (medals)", fmt(r["offensive_reward"]))
    c[1].metric("Defensive reward (medals)", fmt(r["defensive_reward"]))
    c[2].metric("Participants", fmt(r["participants"]))

    members = pd.DataFrame(get_capital_members(raid_id))
    if members.empty:
        st.info("No participant rows were stored for this raid weekend.")
        return
    num_cols(members, ["attacks", "attack_limit", "bonus_attack_limit", "capital_resources_looted"])
    members["limit"] = members["attack_limit"].fillna(0) + members["bonus_attack_limit"].fillna(0)
    members["unused"] = (members["limit"] - members["attacks"]).clip(lower=0)
    members = members.sort_values("capital_resources_looted", ascending=False)

    unused_name = "Unused attacks" if ended else "Attacks left"
    st.subheader("Player participation")
    table = pd.DataFrame({
        "Name": members["name"],
        "Tag": members["player_tag"],
        "Attacks used": members["attacks"],
        "Attack limit": members["limit"],
        unused_name: members["unused"],
        "Capital gold looted": members["capital_resources_looted"],
        "Gold per attack": (
            members["capital_resources_looted"] / members["attacks"].where(members["attacks"] > 0)
        ).round(0),
    })
    int_cols(table, ["Attacks used", "Attack limit", unused_name,
                     "Capital gold looted", "Gold per attack"])
    st.dataframe(table, hide_index=True)

    fig = px.bar(
        members, x="name", y="capital_resources_looted", color="attacks",
        labels={"name": "Player", "capital_resources_looted": "Capital gold looted",
                "attacks": "Attacks"},
        title="Capital gold looted per player",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=members["name"].tolist())
    st.plotly_chart(fig)

    # Current members with no attacks (needs a saved Members snapshot).
    latest = get_latest_snapshot(clan_tag)
    if latest:
        attacked = set(members.loc[members["attacks"] > 0, "player_tag"])
        idle = [m for m in get_snapshot_members(latest["id"]) if m["player_tag"] not in attacked]
        with st.expander(f"Current members with no attacks in this weekend ({len(idle)})"):
            if idle:
                st.dataframe(
                    pd.DataFrame({
                        "Name": [m["name"] for m in idle],
                        "Tag": [m["player_tag"] for m in idle],
                        "Role": [ROLE_LABELS.get(m["role"], m["role"]) for m in idle],
                        "Town Hall": [m["town_hall_level"] for m in idle],
                    }),
                    hide_index=True,
                )
            st.caption(
                "Uses your latest Members snapshot from "
                f"{when(latest['taken_at'])}. This may include people who joined the "
                "clan after this raid weekend."
            )


def _render_history(raids):
    df = pd.DataFrame(raids)
    num_cols(df, [
        "capital_total_loot", "raids_completed", "total_attacks",
        "enemy_districts_destroyed", "offensive_reward", "defensive_reward",
        "participants",
    ])
    table = pd.DataFrame({
        "Weekend start": df["start_time"].apply(when),
        "State": df["state"].map(STATE_LABELS).fillna(df["state"]),
        "Total loot": df["capital_total_loot"],
        "Raids completed": df["raids_completed"],
        "Total attacks": df["total_attacks"],
        "Districts destroyed": df["enemy_districts_destroyed"],
        "Offensive medals": df["offensive_reward"],
        "Defensive medals": df["defensive_reward"],
        "Participants": df["participants"],
    })
    int_cols(table, [c for c in table.columns if c not in ("Weekend start", "State")])
    st.dataframe(table, hide_index=True)

    chart = df.sort_values("start_time").copy()
    chart["Weekend"] = chart["start_time"].apply(lambda s: when(s)[:10])
    left, right = st.columns(2)
    fig = px.bar(chart, x="Weekend", y="capital_total_loot", text="capital_total_loot",
                 labels={"capital_total_loot": "Capital gold looted"},
                 title="Total loot per raid weekend")
    fig.update_xaxes(type="category")
    left.plotly_chart(fig)

    fig = px.line(chart, x="Weekend", y=["total_attacks", "participants"], markers=True,
                  labels={"value": "Count", "variable": ""},
                  title="Attacks and participants per raid weekend")
    fig.update_xaxes(type="category")
    right.plotly_chart(fig)


def render_capital():
    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    st.caption(
        "Each click makes 2 API requests (clan profile and raid weekends) and saves "
        "the results. The API only returns recent weekends, so click regularly to "
        "keep a complete history."
    )
    fetch_button("Fetch & Save Capital Data", "capital_fetch", lambda: _fetch(clan_tag))

    _status(clan_tag)
    raids = get_capital_raids(clan_tag)
    if not raids:
        st.info("No raid weekends saved yet.")
        show_raw("capital_raw")
        return

    tab_weekend, tab_history = st.tabs(["Raid weekend", "History"])
    with tab_weekend:
        _render_weekend(clan_tag, raids)
    with tab_history:
        _render_history(raids)

    show_raw("capital_raw")
