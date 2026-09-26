"""Player Performance: combines saved Members, Wars, CWL and Clan Capital data."""

import pandas as pd
import plotly.express as px
import streamlit as st

from src.analytics_db import get_capital_member_rows, get_war_member_rows
from src.analytics_ui import int_cols, local_dt, num_cols, when
from src.database import get_member_history
from src.ui_helpers import ROLE_LABELS, current_clan_tag

WAR_NUMERIC = [
    "map_position", "town_hall", "attacks_used", "attack_limit", "stars",
    "destruction_total", "three_stars", "opponent_attacks", "best_opp_stars",
    "team_size", "cwl_round",
]
CAP_NUMERIC = ["attacks", "attack_limit", "bonus_attack_limit", "capital_resources_looted"]
HIST_NUMERIC = [
    "town_hall_level", "exp_level", "trophies", "builder_base_trophies",
    "clan_rank", "donations", "donations_received",
]


# ------------------------------------------------------------- prepare

def _prep_war_rows(rows, cwl=False):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    num_cols(df, WAR_NUMERIC)
    df["ended"] = df["state"] == "warEnded"
    df["when"] = pd.to_datetime(df["start_time"].apply(local_dt))
    df["missed"] = (df["attack_limit"] - df["attacks_used"]).clip(lower=0).where(df["ended"])
    df["avg_destruction"] = df["destruction_total"] / df["attacks_used"].where(df["attacks_used"] > 0)

    def label(row):
        if cwl:
            rnd = "?" if pd.isna(row["cwl_round"]) else int(row["cwl_round"])
            return f"{row['cwl_season']} R{rnd}"
        day = "?" if pd.isna(row["when"]) else row["when"].strftime("%Y-%m-%d")
        return f"{day} vs {row['opponent_name'] or '?'}"

    df["label"] = df.apply(label, axis=1)
    return df


def _prep_capital_rows(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    num_cols(df, CAP_NUMERIC)
    df["ended"] = df["state"] == "ended"
    df["limit"] = df["attack_limit"].fillna(0) + df["bonus_attack_limit"].fillna(0)
    df["unused"] = (df["limit"] - df["attacks"]).clip(lower=0).where(df["ended"])
    df["label"] = df["start_time"].apply(lambda s: when(s)[:10])
    return df


def _aggregate_war(df):
    """Per-player totals over FINISHED wars only."""
    if df.empty:
        return pd.DataFrame()
    done = df[df["ended"]]
    if done.empty:
        return pd.DataFrame()
    g = done.groupby("player_tag")
    out = pd.DataFrame({
        "wars": g.size(),
        "attacks": g["attacks_used"].sum(),
        "possible": g["attack_limit"].sum(),
        "missed": g["missed"].sum(),
        "stars": g["stars"].sum(),
        "three_stars": g["three_stars"].sum(),
        "destruction": g["destruction_total"].sum(),
    })
    used = out["attacks"].where(out["attacks"] > 0)
    out["avg_stars"] = out["stars"] / used
    out["avg_destruction"] = out["destruction"] / used
    return out


def _aggregate_capital(df):
    if df.empty:
        return pd.DataFrame()
    done = df[df["ended"]]
    if done.empty:
        return pd.DataFrame()
    g = done.groupby("player_tag")
    out = pd.DataFrame({
        "raids": g["attacks"].apply(lambda s: int((s > 0).sum())),
        "attacks": g["attacks"].sum(),
        "possible": g["limit"].sum(),
        "loot": g["capital_resources_looted"].sum(),
    })
    out["loot_per_raid"] = out["loot"] / out["raids"].where(out["raids"] > 0)
    return out


def _directory(history, wars, cwl, cap):
    """tag -> display name. History is applied last so its names win."""
    names = {}
    for df in (cap, cwl, wars, history):
        if df.empty:
            continue
        for tag, name in zip(df["player_tag"], df["name"]):
            names[tag] = name or tag
    return names


def _row(agg, tag):
    return agg.loc[tag] if (not agg.empty and tag in agg.index) else None


def _v(row, key, decimals=0):
    if row is None or pd.isna(row[key]):
        return "N/A"
    return f"{row[key]:,.{decimals}f}"


def _ordered(labels):
    return list(dict.fromkeys(labels))


def _player_history(history, tag):
    if history.empty:
        return history
    return history[history["player_tag"] == tag].sort_values("snapshot_id")


# --------------------------------------------------------- player detail

def _war_block(title, row, empty_note):
    st.markdown(title)
    c = st.columns(5)
    if row is None:
        c[0].metric("Played", "N/A")
        st.caption(empty_note)
        return
    avg = _v(row, "avg_destruction", 1)
    c[0].metric("Played", _v(row, "wars"))
    c[1].metric("Attacks used / possible", f"{_v(row, 'attacks')} / {_v(row, 'possible')}")
    c[2].metric("Missed attacks", _v(row, "missed"))
    c[3].metric("Stars", _v(row, "stars"))
    c[4].metric("Avg destruction", "N/A" if avg == "N/A" else f"{avg}%")


def _render_overview(tag, history, war_row, cwl_row, cap_row, total_raids):
    st.markdown("**Clan membership (latest saved snapshot)**")
    mine = _player_history(history, tag)
    if mine.empty:
        st.caption("No Members snapshot includes this player.")
    else:
        last = mine.iloc[-1]

        def whole(key):
            return "N/A" if pd.isna(last[key]) else f"{int(last[key]):,}"

        c = st.columns(5)
        c[0].metric("Town Hall", whole("town_hall_level"))
        c[1].metric("Role", ROLE_LABELS.get(last["role"], last["role"]))
        c[2].metric("Trophies", whole("trophies"))
        c[3].metric("Donated (season)", whole("donations"))
        c[4].metric("Received (season)", whole("donations_received"))
        st.caption(f"From the snapshot taken {when(last['taken_at'])}.")

    _war_block("**Clan wars (finished wars only)**", war_row,
               "No finished war with this player saved yet.")
    _war_block("**Clan War League (finished rounds only)**", cwl_row,
               "No finished CWL round with this player saved yet.")

    st.markdown("**Clan Capital (finished raid weekends only)**")
    c = st.columns(4)
    if cap_row is None:
        c[0].metric("Raid weekends", "N/A")
        st.caption("No finished raid weekend with this player saved yet.")
    else:
        c[0].metric("Weekends with attacks", f"{_v(cap_row, 'raids')} of {total_raids}")
        c[1].metric("Attacks", f"{_v(cap_row, 'attacks')} / {_v(cap_row, 'possible')}")
        c[2].metric("Total capital gold", _v(cap_row, "loot"))
        c[3].metric("Gold per weekend attended", _v(cap_row, "loot_per_raid"))


def _render_members_tab(tag, history):
    ph = _player_history(history, tag)
    if ph.empty:
        st.info("No Members snapshots include this player. Save snapshots on the Members page.")
        return
    if len(ph) < 2:
        st.info("Trend charts need at least 2 saved snapshots for this player.")
    else:
        left, right = st.columns(2)
        long = ph.melt(id_vars=["Time"], value_vars=["trophies", "builder_base_trophies"],
                       var_name="Type", value_name="Trophies")
        long["Type"] = long["Type"].map(
            {"trophies": "Home village", "builder_base_trophies": "Builder Base"}
        )
        fig = px.line(long, x="Time", y="Trophies", color="Type", markers=True,
                      title="Trophies over time")
        left.plotly_chart(fig)

        long = ph.melt(id_vars=["Time"], value_vars=["donations", "donations_received"],
                       var_name="Type", value_name="Amount")
        long["Type"] = long["Type"].map({"donations": "Donated", "donations_received": "Received"})
        fig = px.line(long, x="Time", y="Amount", color="Type", markers=True,
                      title="Donations over time (season counters reset monthly)")
        right.plotly_chart(fig)

    table = pd.DataFrame({
        "Snapshot time": ph["Time"].dt.strftime("%Y-%m-%d %H:%M"),
        "Town Hall": ph["town_hall_level"], "Trophies": ph["trophies"],
        "Builder trophies": ph["builder_base_trophies"], "Rank": ph["clan_rank"],
        "Donated": ph["donations"], "Received": ph["donations_received"],
    })
    int_cols(table, [c for c in table.columns if c != "Snapshot time"])
    st.dataframe(table, hide_index=True)


def _render_war_tab(tag, df, kind):
    """kind is 'war' or 'CWL'. Shared by both war types."""
    mine = df[df["player_tag"] == tag].sort_values("when") if not df.empty else df
    if mine.empty:
        page = "Wars" if kind == "war" else "CWL"
        st.info(f"No saved {kind} data for this player yet. Fetch it on the {page} page.")
        return

    done = mine[mine["ended"]]
    pending = len(mine) - len(done)
    if pending:
        st.caption(
            f"{pending} in-progress {kind} row(s) are in the table but excluded from "
            "charts and totals until they finish."
        )

    left = (mine["attack_limit"] - mine["attacks_used"]).clip(lower=0)
    name_col = "War" if kind == "war" else "Round"
    table = pd.DataFrame({
        name_col: mine["label"],
        "State": mine["ended"].map({True: "Finished", False: "In progress"}),
        "Map position": mine["map_position"],
        "Attacks used": mine["attacks_used"],
        "Attacks allowed": mine["attack_limit"],
        "Missed": mine["missed"],
        "Attacks left": left.where(~mine["ended"]),
        "Stars": mine["stars"],
        "3-star attacks": mine["three_stars"],
        "Avg destruction %": mine["avg_destruction"].round(1),
        "Times attacked": mine["opponent_attacks"],
        "Best defense (stars conceded)": mine["best_opp_stars"],
    })
    int_cols(table, ["Map position", "Attacks used", "Attacks allowed", "Missed",
                     "Attacks left", "Stars", "3-star attacks", "Times attacked",
                     "Best defense (stars conceded)"])
    st.dataframe(table, hide_index=True)

    if done.empty:
        return
    order = _ordered(done["label"])
    left_col, right_col = st.columns(2)
    if kind == "war":
        fig = px.bar(done, x="label", y="stars", text="stars",
                     hover_data=["attacks_used", "missed"],
                     labels={"label": "War", "stars": "Stars"}, title="Stars per war")
    else:
        fig = px.bar(done, x="label", y="stars", text="stars", color="cwl_season",
                     hover_data=["attacks_used", "missed"],
                     labels={"label": "Round", "stars": "Stars", "cwl_season": "Season"},
                     title="Stars per CWL round")
    fig.update_xaxes(categoryorder="array", categoryarray=order)
    left_col.plotly_chart(fig)

    dest = done.dropna(subset=["avg_destruction"])
    if dest.empty:
        right_col.info("No attacks were made in the finished rounds, so there is no destruction to chart.")
    else:
        fig = px.line(dest, x="label", y="avg_destruction", markers=True,
                      labels={"label": name_col, "avg_destruction": "Avg destruction %"},
                      title="Average destruction % per attack")
        fig.update_xaxes(categoryorder="array", categoryarray=order)
        right_col.plotly_chart(fig)

    if kind == "CWL":
        seasons = done.groupby("cwl_season").agg(
            rounds=("war_id", "nunique"), attacks=("attacks_used", "sum"),
            missed=("missed", "sum"), stars=("stars", "sum"),
            three=("three_stars", "sum"),
        ).reset_index()
        seasons.columns = ["Season", "Rounds", "Attacks used", "Missed", "Stars", "3-star attacks"]
        st.subheader("By season")
        st.dataframe(seasons, hide_index=True)


def _render_capital_tab(tag, cap):
    mine = cap[cap["player_tag"] == tag].sort_values("start_time") if not cap.empty else cap
    if mine.empty:
        st.info("No saved raid weekend data for this player yet. Fetch it on the Clan Capital page.")
        return
    table = pd.DataFrame({
        "Weekend start": mine["label"],
        "State": mine["ended"].map({True: "Ended", False: "Ongoing"}),
        "Attacks used": mine["attacks"],
        "Attack limit": mine["limit"],
        "Unused attacks": mine["unused"],
        "Capital gold looted": mine["capital_resources_looted"],
        "Gold per attack": (
            mine["capital_resources_looted"] / mine["attacks"].where(mine["attacks"] > 0)
        ).round(0),
    })
    int_cols(table, [c for c in table.columns if c not in ("Weekend start", "State")])
    st.dataframe(table, hide_index=True)

    done = mine[mine["ended"]]
    if done.empty:
        return
    order = _ordered(done["label"])
    left, right = st.columns(2)
    fig = px.bar(done, x="label", y="capital_resources_looted", text="capital_resources_looted",
                 labels={"label": "Weekend", "capital_resources_looted": "Capital gold looted"},
                 title="Capital gold looted per weekend")
    fig.update_xaxes(categoryorder="array", categoryarray=order)
    left.plotly_chart(fig)

    long = done.melt(id_vars=["label"], value_vars=["attacks", "limit"],
                     var_name="Type", value_name="Attacks")
    long["Type"] = long["Type"].map({"attacks": "Used", "limit": "Allowed"})
    fig = px.bar(long, x="label", y="Attacks", color="Type", barmode="group",
                 labels={"label": "Weekend"}, title="Attacks used vs allowed")
    fig.update_xaxes(categoryorder="array", categoryarray=order)
    right.plotly_chart(fig)


def _render_player(tag, history, wars, cwl, cap, war_agg, cwl_agg, cap_agg):
    total_raids = int(cap[cap["ended"]]["raid_id"].nunique()) if not cap.empty else 0
    tabs = st.tabs(["Overview", "Trophies & donations", "Wars", "CWL", "Clan Capital"])
    with tabs[0]:
        _render_overview(tag, history, _row(war_agg, tag), _row(cwl_agg, tag),
                         _row(cap_agg, tag), total_raids)
    with tabs[1]:
        _render_members_tab(tag, history)
    with tabs[2]:
        _render_war_tab(tag, wars, "war")
    with tabs[3]:
        _render_war_tab(tag, cwl, "CWL")
    with tabs[4]:
        _render_capital_tab(tag, cap)


# -------------------------------------------------------- clan comparison

def _leaderboard(names, current_tags, history, war_agg, cwl_agg, cap_agg):
    base = pd.DataFrame({"Name": pd.Series(names)})
    base.index.name = "Tag"
    if current_tags is not None:
        base["In clan now"] = ["Yes" if t in current_tags else "No" for t in base.index]

    if not history.empty:
        last = (history.sort_values("snapshot_id").drop_duplicates("player_tag", keep="last")
                .set_index("player_tag"))
        base["Town Hall"] = last["town_hall_level"]
        base["Role"] = last["role"].map(ROLE_LABELS).fillna(last["role"])
        base["Trophies"] = last["trophies"]
        base["Donated"] = last["donations"]
        base["Received"] = last["donations_received"]

    for prefix, agg, unit in (("War", war_agg, "wars"), ("CWL", cwl_agg, "rounds")):
        if agg.empty:
            continue
        base[f"{prefix} {unit}"] = agg["wars"]
        base[f"{prefix} attacks used"] = agg["attacks"]
        base[f"{prefix} missed attacks"] = agg["missed"]
        base[f"{prefix} stars"] = agg["stars"]
        base[f"{prefix} 3-star attacks"] = agg["three_stars"]
        base[f"{prefix} avg stars/attack"] = agg["avg_stars"].round(2)
        base[f"{prefix} avg destruction %"] = agg["avg_destruction"].round(1)

    if not cap_agg.empty:
        base["Capital weekends attended"] = cap_agg["raids"]
        base["Capital attacks"] = cap_agg["attacks"]
        base["Capital gold looted"] = cap_agg["loot"]
        base["Capital gold / weekend"] = cap_agg["loot_per_raid"].round(0)

    text = {"Name", "In clan now", "Role"}
    int_cols(base, [c for c in base.columns if c not in text and "avg" not in c])
    return base.reset_index()


def _render_comparison(names, current_tags, history, war_agg, cwl_agg, cap_agg):
    board = _leaderboard(names, current_tags, history, war_agg, cwl_agg, cap_agg)
    if current_tags is not None and st.checkbox(
        "Only players in the latest snapshot", value=True, key="perf_only_current"
    ):
        board = board[board["In clan now"] == "Yes"]

    st.dataframe(board, hide_index=True)
    st.download_button("Download as CSV", board.to_csv(index=False).encode("utf-8-sig"),
                       file_name="player_performance.csv", mime="text/csv")
    st.caption(
        "Blank = no saved data of that type for the player (not zero). War, CWL and "
        "Capital totals cover finished events only."
    )

    numeric = [c for c in board.columns if c not in ("Name", "Tag", "In clan now", "Role")]
    if not numeric:
        return
    metric = st.selectbox("Chart metric", numeric, key="perf_metric")
    chart = board.dropna(subset=[metric]).copy()
    if chart.empty:
        st.info("No data for this metric yet.")
        return
    chart[metric] = chart[metric].astype(float)
    chart = chart.sort_values(metric, ascending=False)
    fig = px.bar(chart, x="Name", y=metric, hover_data=["Tag"], title=f"{metric} by player")
    fig.update_xaxes(tickangle=-60, categoryorder="array", categoryarray=chart["Name"].tolist())
    st.plotly_chart(fig)


# ------------------------------------------------------------------ page

def render_performance():
    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    history = pd.DataFrame(get_member_history(clan_tag))
    if not history.empty:
        num_cols(history, HIST_NUMERIC)
        history["Time"] = pd.to_datetime(history["taken_at"].apply(local_dt))
    wars = _prep_war_rows(get_war_member_rows(clan_tag, "regular"))
    cwl = _prep_war_rows(get_war_member_rows(clan_tag, "cwl"), cwl=True)
    cap = _prep_capital_rows(get_capital_member_rows(clan_tag))

    if history.empty and wars.empty and cwl.empty and cap.empty:
        st.info(
            "Nothing is saved yet. Save a snapshot on the Members page, and fetch "
            "data on the Wars, CWL and Clan Capital pages. This page combines them."
        )
        return

    st.caption(
        "Built only from data saved in your local database: "
        f"{history['snapshot_id'].nunique() if not history.empty else 0} member snapshot(s), "
        f"{wars['war_id'].nunique() if not wars.empty else 0} war(s), "
        f"{cwl['war_id'].nunique() if not cwl.empty else 0} CWL war(s), "
        f"{cap['raid_id'].nunique() if not cap.empty else 0} raid weekend(s)."
    )

    names = _directory(history, wars, cwl, cap)
    current_tags = None
    if not history.empty:
        latest_id = history["snapshot_id"].max()
        current_tags = set(history.loc[history["snapshot_id"] == latest_id, "player_tag"])

    war_agg, cwl_agg, cap_agg = _aggregate_war(wars), _aggregate_war(cwl), _aggregate_capital(cap)

    tab_player, tab_compare = st.tabs(["Player detail", "Clan comparison"])
    with tab_player:
        options = sorted(names, key=lambda t: names[t].lower())

        def label(tag):
            note = "" if current_tags is None or tag in current_tags else " - not in latest snapshot"
            return f"{names[tag]} ({tag}){note}"

        tag = st.selectbox("Player", options, format_func=label, key="perf_player")
        _render_player(tag, history, wars, cwl, cap, war_agg, cwl_agg, cap_agg)
    with tab_compare:
        _render_comparison(names, current_tags, history, war_agg, cwl_agg, cap_agg)
