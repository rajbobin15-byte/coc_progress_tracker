"""Wars page: current war, per-player results and war history."""

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics_db import (
    get_latest_meta,
    get_war_attacks,
    get_war_log,
    get_war_members,
    get_wars,
    save_clan_meta,
    save_war,
    save_war_log,
)
from src.analytics_ui import (
    RESULT_LABELS,
    STATE_LABELS,
    fetch_button,
    int_cols,
    is_past,
    num_cols,
    pct,
    show_raw,
    when,
)
from src.coc_api import (
    ClashAPIError,
    ManualTokenRequired,
    RateLimitError,
    WarLogPrivateError,
    get_clan_info,
    get_current_war,
    get_war_log_items,
)
from src.ui_helpers import current_clan_tag, fmt


# ---------------------------------------------------------------- fetch

def _problem(exc):
    if isinstance(exc, ManualTokenRequired):
        return "manual_token", exc
    kind = "warning" if isinstance(exc, WarLogPrivateError) else "error"
    return kind, f"**{exc.title}**\n\n{exc}"


def _fetch(clan_tag):
    results = []
    clan = get_clan_info()
    save_clan_meta(clan)

    if clan.get("isWarLogPublic") is False:
        results.append((
            "warning",
            "**Your clan's war log is private.** The API does not return war data "
            "for private war logs. A leader or co-leader can change it in the game: "
            "Clan Settings > War Log > Public. Then click the button again.",
        ))
        return results

    try:
        war = get_current_war()
        st.session_state["wars_raw"] = war
        state = war.get("state")
        if state in (None, "notInWar"):
            results.append(("info", "Your clan is not in a regular war right now."))
        else:
            war_id = save_war(war, clan_tag)
            if war_id is None:
                results.append((
                    "warning",
                    "The API returned a war but your clan is not one of its sides, "
                    "so nothing was saved.",
                ))
            else:
                opponent = (war.get("opponent") or {}).get("name", "unknown opponent")
                results.append((
                    "success",
                    f"Current war saved: vs {opponent} "
                    f"({STATE_LABELS.get(state, state)}).",
                ))
    except ClashAPIError as exc:
        results.append(_problem(exc))
        if isinstance(exc, RateLimitError):
            return results

    try:
        items = get_war_log_items()
        new = save_war_log(clan_tag, items)
        results.append((
            "success", f"War log: {len(items)} entries read, {new} new entries saved."
        ))
    except ClashAPIError as exc:
        results.append(_problem(exc))
    return results


# --------------------------------------------------------------- display

def _record(clan_tag):
    meta = get_latest_meta(clan_tag)
    if not meta:
        return
    public = meta["is_war_log_public"]
    cols = st.columns(5)
    cols[0].metric("War wins", fmt(meta["war_wins"]))
    cols[1].metric("War losses", fmt(meta["war_losses"]))
    cols[2].metric("War ties", fmt(meta["war_ties"]))
    cols[3].metric("Win streak", fmt(meta["war_win_streak"]))
    cols[4].metric(
        "War log", "N/A" if public is None else ("Public" if public else "Private")
    )
    st.caption(f"Clan war record as of {when(meta['taken_at'])}.")


def _war_label(w):
    opponent = w["opponent_name"] or "unknown opponent"
    return (
        f"{when(w['start_time'])} - vs {opponent} - "
        f"{STATE_LABELS.get(w['state'], w['state'])}"
    )


def _member_frame(members):
    df = pd.DataFrame(members)
    num_cols(df, [
        "map_position", "town_hall", "attacks_used", "attack_limit", "stars",
        "destruction_total", "three_stars", "opponent_attacks", "best_opp_stars",
    ])
    return df.sort_values("map_position")


def _member_table(df, ended):
    left_name = "Missed attacks" if ended else "Attacks left"
    left = (df["attack_limit"] - df["attacks_used"]).clip(lower=0)
    table = pd.DataFrame({
        "Pos": df["map_position"],
        "Name": df["name"],
        "Tag": df["player_tag"],
        "Town Hall": df["town_hall"],
        "Attacks used": df["attacks_used"],
        "Attacks allowed": df["attack_limit"],
        left_name: left,
        "Stars": df["stars"],
        "3-star attacks": df["three_stars"],
        "Avg destruction %": (
            df["destruction_total"] / df["attacks_used"].where(df["attacks_used"] > 0)
        ).round(1),
        "Total destruction %": df["destruction_total"].round(1),
        "Times attacked": df["opponent_attacks"],
        "Best defense (stars conceded)": df["best_opp_stars"],
    })
    int_cols(table, [
        "Pos", "Town Hall", "Attacks used", "Attacks allowed", left_name, "Stars",
        "3-star attacks", "Times attacked", "Best defense (stars conceded)",
    ])
    return table


def _attack_table(attacks):
    atk = pd.DataFrame(attacks)
    num_cols(atk, [
        "attack_order", "attacker_position", "attacker_th", "defender_position",
        "defender_th", "stars", "destruction", "duration",
    ])
    table = pd.DataFrame({
        "Order": atk["attack_order"],
        "Attacker": atk["attacker_name"],
        "Att. pos": atk["attacker_position"],
        "Att. TH": atk["attacker_th"],
        "Defender": atk["defender_name"],
        "Def. pos": atk["defender_position"],
        "Def. TH": atk["defender_th"],
        "TH diff (att - def)": atk["attacker_th"] - atk["defender_th"],
        "Stars": atk["stars"],
        "Destruction %": atk["destruction"],
        "Duration (s)": atk["duration"],
    }).sort_values("Order")
    int_cols(table, [
        "Order", "Att. pos", "Att. TH", "Def. pos", "Def. TH",
        "TH diff (att - def)", "Stars", "Duration (s)",
    ])
    return table, atk


def _render_war_details(wars):
    if not wars:
        st.info(
            "No wars saved yet. Click **Fetch & Save War Data** while your clan is "
            "in a war (preparation, battle day, or just after it ends)."
        )
        return

    by_id = {w["id"]: w for w in wars}
    war_id = st.selectbox(
        "War (newest first)", list(by_id),
        format_func=lambda i: _war_label(by_id[i]), key="wars_selected",
    )
    w = by_id[war_id]
    ended = w["state"] == "warEnded"

    if not ended and is_past(w["end_time"]):
        st.warning(
            "According to the clock this war has ended, but the saved data is from "
            f"before that (last updated {when(w['last_updated_at'])}). Click "
            "**Fetch & Save War Data** to store the final result and missed attacks. "
            "This only works until the next war starts."
        )

    result = RESULT_LABELS.get(w["result"]) if ended else "In progress"
    c = st.columns(4)
    c[0].metric("Opponent", w["opponent_name"] or "N/A")
    c[1].metric("War state", STATE_LABELS.get(w["state"], w["state"] or "N/A"))
    c[2].metric("Team size", f"{w['team_size']} v {w['team_size']}" if w["team_size"] else "N/A")
    c[3].metric("Result", result or "N/A")

    c = st.columns(4)
    c[0].metric("Our stars", fmt(w["clan_stars"]))
    c[1].metric("Opponent stars", fmt(w["opp_stars"]))
    c[2].metric("Our destruction", pct(w["clan_destruction"]))
    c[3].metric("Opponent destruction", pct(w["opp_destruction"]))

    c = st.columns(4)
    c[0].metric("Our attacks used", fmt(w["clan_attacks"]))
    c[1].metric("Opponent attacks used", fmt(w["opp_attacks"]))
    c[2].metric("Attacks per member", fmt(w["attacks_per_member"]))
    c[3].metric("Ended" if ended else "Ends", when(w["end_time"]))
    st.caption(
        f"Battle day starts {when(w['start_time'])}. Data last updated from the API "
        f"at {when(w['last_updated_at'])}."
    )

    members = get_war_members(war_id)
    if not members:
        st.info("No player rows were stored for this war.")
        return
    df = _member_frame(members)

    st.subheader("Per-player results")
    st.dataframe(_member_table(df, ended), hide_index=True)
    st.caption(
        "Stars are the raw stars from each attack, so player totals can exceed the "
        "clan total (which only counts new stars per enemy base). "
        + ("Missed attacks = allowed - used." if ended
           else "The war is not over, so attacks left are not yet 'missed'.")
    )

    attacks = get_war_attacks(war_id)
    left, right = st.columns(2)

    fig = px.bar(
        df, x="name", y="stars",
        color=df["attacks_used"].fillna(0).astype(int).astype(str),
        labels={"name": "Player", "stars": "Stars", "color": "Attacks used"},
        title="Stars earned per player",
    )
    fig.update_xaxes(categoryorder="array", categoryarray=df["name"].tolist())
    left.plotly_chart(fig)

    if attacks:
        table, atk = _attack_table(attacks)
        star_values = atk["stars"].dropna().astype(int)
        dist = star_values.value_counts().reindex([0, 1, 2, 3], fill_value=0).reset_index()
        dist.columns = ["Stars per attack", "Attacks"]
        fig = px.bar(dist, x="Stars per attack", y="Attacks", text="Attacks",
                     title="Attack results by stars")
        fig.update_xaxes(type="category")
        right.plotly_chart(fig)

        st.subheader("Every attack")
        st.dataframe(table, hide_index=True)
    else:
        right.info("No attacks have been made in this war yet.")


def _render_history(clan_tag, wars):
    st.subheader("War log (from the API)")
    log = pd.DataFrame(get_war_log(clan_tag))
    if log.empty:
        st.info("No war log entries saved yet. Click **Fetch & Save War Data**.")
    else:
        num_cols(log, [
            "team_size", "attacks_per_member", "clan_stars", "clan_destruction",
            "clan_attacks", "opp_stars", "opp_destruction", "exp_earned",
        ])
        c = st.columns(4)
        c[0].metric("Wars in log", len(log))
        c[1].metric("Wins", int((log["result"] == "win").sum()))
        c[2].metric("Losses", int((log["result"] == "lose").sum()))
        c[3].metric("Ties", int((log["result"] == "tie").sum()))

        table = pd.DataFrame({
            "Ended": log["end_time"].apply(when),
            "Result": log["result"].map(RESULT_LABELS).fillna("N/A"),
            "Opponent": log["opponent_name"].fillna("N/A"),
            "Team size": log["team_size"],
            "Attacks/member": log["attacks_per_member"],
            "Our stars": log["clan_stars"],
            "Our destruction %": log["clan_destruction"].round(1),
            "Our attacks": log["clan_attacks"],
            "Opp. stars": log["opp_stars"],
            "Opp. destruction %": log["opp_destruction"].round(1),
            "Clan XP": log["exp_earned"],
        })
        int_cols(table, ["Team size", "Attacks/member", "Our stars", "Our attacks",
                         "Opp. stars", "Clan XP"])
        st.dataframe(table, hide_index=True)
        st.caption(
            "The API war log is clan-level only. Entries without opponent details are "
            "usually Clan War League rounds."
        )

        chart = log.sort_values("end_time").copy()
        chart["War ended"] = chart["end_time"].apply(when)
        long = chart.melt(
            id_vars=["War ended"], value_vars=["clan_stars", "opp_stars"],
            var_name="Side", value_name="Stars",
        )
        long["Side"] = long["Side"].map({"clan_stars": "Our clan", "opp_stars": "Opponent"})
        fig = px.bar(long, x="War ended", y="Stars", color="Side", barmode="group",
                     title="Stars per war: our clan vs opponent")
        fig.update_xaxes(type="category")
        st.plotly_chart(fig)

    st.subheader("Wars saved with player details")
    if not wars:
        st.info("None yet.")
        return
    table = pd.DataFrame({
        "Battle day start": [when(w["start_time"]) for w in wars],
        "Opponent": [w["opponent_name"] or "N/A" for w in wars],
        "State": [STATE_LABELS.get(w["state"], w["state"]) for w in wars],
        "Result": [RESULT_LABELS.get(w["result"], "") for w in wars],
        "Team size": [w["team_size"] for w in wars],
        "Our stars": [w["clan_stars"] for w in wars],
        "Opp. stars": [w["opp_stars"] for w in wars],
        "Our destruction %": [
            round(w["clan_destruction"], 1) if w["clan_destruction"] is not None else None
            for w in wars
        ],
        "Opp. destruction %": [
            round(w["opp_destruction"], 1) if w["opp_destruction"] is not None else None
            for w in wars
        ],
    })
    int_cols(table, ["Team size", "Our stars", "Opp. stars"])
    st.dataframe(table, hide_index=True)


# ------------------------------------------------------------------ page

def render_wars():
    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    st.caption(
        "Each click makes up to 3 API requests (clan profile, current war, war log) "
        "and saves the results to the local database. Click during the war and once "
        "after it ends so the final per-player results are kept."
    )
    fetch_button("Fetch & Save War Data", "wars_fetch", lambda: _fetch(clan_tag))

    _record(clan_tag)
    wars = get_wars(clan_tag, "regular")

    tab_details, tab_history = st.tabs(["Current / selected war", "War history"])
    with tab_details:
        _render_war_details(wars)
    with tab_history:
        _render_history(clan_tag, wars)

    show_raw("wars_raw")
