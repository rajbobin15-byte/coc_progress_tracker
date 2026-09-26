"""Members page: table, filters, charts and player history from SQLite snapshots."""

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from src.database import get_member_history, get_snapshot_list, get_snapshot_members
from src.ui_helpers import (
    ROLE_LABELS,
    current_clan_tag,
    fetch_and_save_snapshot,
    local_time,
)

NUMERIC_COLUMNS = [
    "clan_rank",
    "previous_clan_rank",
    "town_hall_level",
    "exp_level",
    "trophies",
    "builder_base_trophies",
    "donations",
    "donations_received",
]

ROLE_ORDER = ["Leader", "Co-Leader", "Elder", "Member"]

HISTORY_METRICS = {
    "Trophies": "trophies",
    "Builder Base trophies": "builder_base_trophies",
    "Donations given": "donations",
    "Donations received": "donations_received",
    "Clan rank": "clan_rank",
    "XP level": "exp_level",
    "Town Hall": "town_hall_level",
}


# ---------------------------------------------------------------- helpers

def _to_frame(rows):
    """List of member dicts -> DataFrame with numeric columns, or None if empty."""
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["role_label"] = df["role"].map(ROLE_LABELS).fillna(df["role"])
    return df


def _avg(series, decimals=0):
    values = series.dropna()
    return "N/A" if values.empty else f"{values.mean():,.{decimals}f}"


def _total(series):
    return f"{int(series.sum()):,}"


def _build_table(current, previous):
    """The display table for one snapshot, with change columns if `previous` exists."""
    table = pd.DataFrame(
        {
            "Rank": current["clan_rank"],
            "Name": current["name"],
            "Tag": current["player_tag"],
            "Role": current["role_label"],
            "Town Hall": current["town_hall_level"],
            "XP Level": current["exp_level"],
            "League": current["league_name"],
            "Trophies": current["trophies"],
            "Builder Trophies": current["builder_base_trophies"],
            "Donated": current["donations"],
            "Received": current["donations_received"],
            "Donation balance": current["donations"] - current["donations_received"],
            # Positive = moved up compared with the previous season.
            "Rank vs last season": (
                current["previous_clan_rank"] - current["clan_rank"]
            ).where(current["previous_clan_rank"] > 0),
        }
    )

    if previous is not None:
        prev = previous.set_index("player_tag")
        tags = current["player_tag"]
        table["Trophy change"] = current["trophies"] - tags.map(prev["trophies"])
        donated_change = current["donations"] - tags.map(prev["donations"])
        # Donation counters reset every season; a negative change is a reset, not data.
        table["Donated change"] = donated_change.where(donated_change >= 0)

    text_columns = {"Name", "Tag", "Role", "League"}
    for col in table.columns:
        if col not in text_columns:
            table[col] = table[col].astype("Int64")  # whole numbers, blanks stay blank
    return table


def _apply_filters(table):
    c1, c2, c3 = st.columns(3)
    search = c1.text_input("Search name or tag", key="members_search").strip().lower()

    roles = sorted(
        table["Role"].dropna().unique(),
        key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else 99,
    )
    chosen_roles = c2.multiselect("Role", roles, key="members_roles")

    town_halls = sorted({int(x) for x in table["Town Hall"].dropna()}, reverse=True)
    chosen_ths = c3.multiselect("Town Hall", town_halls, key="members_ths")

    mask = pd.Series(True, index=table.index)
    if search:
        mask &= table["Name"].str.lower().str.contains(search, regex=False, na=False)
        mask |= table["Tag"].str.lower().str.contains(search, regex=False, na=False)
    if chosen_roles:
        mask &= table["Role"].isin(chosen_roles)
    if chosen_ths:
        mask &= table["Town Hall"].isin(chosen_ths)
    return table[mask]


def _show(title, frame):
    if not frame.empty:
        st.markdown(f"**{title}**")
        st.dataframe(frame, hide_index=True)


def _render_roster_changes(current, previous, previous_time):
    with st.expander(f"Roster changes since the snapshot from {previous_time}"):
        joined = current[~current["player_tag"].isin(previous["player_tag"])]
        left = previous[~previous["player_tag"].isin(current["player_tag"])]
        both = current.merge(previous, on="player_tag", suffixes=("", "_prev"))
        upgraded = both[both["town_hall_level"] > both["town_hall_level_prev"]]
        role_changed = both[both["role"] != both["role_prev"]]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Joined", len(joined))
        c2.metric("Left", len(left))
        c3.metric("Town Hall upgrades", len(upgraded))
        c4.metric("Role changes", len(role_changed))

        _show(
            "Joined",
            pd.DataFrame(
                {
                    "Name": joined["name"],
                    "Tag": joined["player_tag"],
                    "Town Hall": joined["town_hall_level"].astype("Int64"),
                }
            ),
        )
        _show(
            "Left",
            pd.DataFrame(
                {
                    "Name": left["name"],
                    "Tag": left["player_tag"],
                    "Town Hall": left["town_hall_level"].astype("Int64"),
                }
            ),
        )
        _show(
            "Town Hall upgrades",
            pd.DataFrame(
                {
                    "Name": upgraded["name"],
                    "Tag": upgraded["player_tag"],
                    "From": upgraded["town_hall_level_prev"].astype("Int64"),
                    "To": upgraded["town_hall_level"].astype("Int64"),
                }
            ),
        )
        _show(
            "Role changes",
            pd.DataFrame(
                {
                    "Name": role_changed["name"],
                    "Tag": role_changed["player_tag"],
                    "From": role_changed["role_label_prev"],
                    "To": role_changed["role_label"],
                }
            ),
        )


# ------------------------------------------------------------------- tabs

def _render_table_tab(snapshot_id, current, previous, previous_time):
    table = _build_table(current, previous)
    filtered = _apply_filters(table)

    st.caption(
        f"Showing {len(filtered)} of {len(table)} members. Click a column header to sort. "
        "'Rank vs last season': positive means the player moved up."
    )
    height = min(35 * (len(filtered) + 1) + 3, 900)
    st.dataframe(filtered, hide_index=True, height=height)

    st.download_button(
        "Download table as CSV",
        filtered.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"members_snapshot_{snapshot_id}.csv",
        mime="text/csv",
    )

    if previous is None:
        st.caption(
            "Change columns and roster changes appear once there is an earlier "
            "snapshot to compare with."
        )
    else:
        st.caption(
            "'Donated change' is blank for new members and after a season reset "
            "(donation counters restart every season)."
        )
        _render_roster_changes(current, previous, previous_time)


def _render_charts_tab(df):
    left, right = st.columns(2)

    th = (
        df.dropna(subset=["town_hall_level"])
        .groupby("town_hall_level")
        .size()
        .reset_index(name="Members")
    )
    th["Town Hall"] = "TH" + th["town_hall_level"].astype(int).astype(str)
    fig = px.bar(th, x="Town Hall", y="Members", text="Members",
                 title="Town Hall distribution")
    fig.update_xaxes(type="category")
    left.plotly_chart(fig)

    ranked = df.dropna(subset=["trophies"]).sort_values("trophies", ascending=False)
    fig = px.bar(
        ranked,
        x="name",
        y="trophies",
        hover_data=["player_tag", "town_hall_level"],
        labels={"name": "Member", "trophies": "Trophies"},
        title="Trophies by member",
    )
    fig.update_xaxes(
        tickangle=-60, categoryorder="array", categoryarray=ranked["name"].tolist()
    )
    right.plotly_chart(fig)

    by_donated = df.sort_values("donations", ascending=False)
    donations = by_donated.melt(
        id_vars=["name"],
        value_vars=["donations", "donations_received"],
        var_name="Type",
        value_name="Amount",
    )
    donations["Type"] = donations["Type"].map(
        {"donations": "Donated", "donations_received": "Received"}
    )
    fig = px.bar(
        donations,
        x="name",
        y="Amount",
        color="Type",
        barmode="group",
        labels={"name": "Member"},
        title="Donations (current season counters): given vs received",
    )
    fig.update_xaxes(
        tickangle=-60, categoryorder="array", categoryarray=by_donated["name"].tolist()
    )
    st.plotly_chart(fig)


def _render_history_tab(clan_tag):
    history = pd.DataFrame(get_member_history(clan_tag))
    if history.empty:
        st.info("No history stored yet.")
        return

    for col in HISTORY_METRICS.values():
        history[col] = pd.to_numeric(history[col], errors="coerce")
    history["Time"] = history["taken_at"].apply(
        lambda s: datetime.fromisoformat(s).astimezone().replace(tzinfo=None)
    )

    latest_id = history["snapshot_id"].max()
    latest = history[history["snapshot_id"] == latest_id]
    in_latest = set(latest["player_tag"])
    last_seen = (
        history.sort_values("snapshot_id")
        .drop_duplicates("player_tag", keep="last")
        .set_index("player_tag")["name"]
    )
    labels = {
        tag: f"{name} ({tag})" + ("" if tag in in_latest else " - left clan")
        for tag, name in last_seen.items()
    }
    options = sorted(labels, key=lambda t: labels[t].lower())
    default = [latest.sort_values("clan_rank")["player_tag"].iloc[0]]

    c1, c2 = st.columns([3, 2])
    selected = c1.multiselect(
        "Players", options, default=default, format_func=labels.get,
        key="members_history_players",
    )
    metric_label = c2.selectbox("Metric", list(HISTORY_METRICS), key="members_history_metric")

    st.caption(
        "History comes only from snapshots you saved; the API does not provide past "
        "data. Donation counters reset every season, so a drop in donations means a "
        "new season started."
    )

    if not selected:
        st.info("Select at least one player.")
        return

    metric = HISTORY_METRICS[metric_label]
    data = history[history["player_tag"].isin(selected)].copy()
    data["Player"] = data["player_tag"].map(labels)

    if data.groupby("player_tag").size().max() < 2:
        st.info(
            "A history chart needs at least 2 saved snapshots that include the "
            "selected player. Save another snapshot later to see how values change."
        )
    else:
        fig = px.line(
            data, x="Time", y=metric, color="Player", markers=True,
            labels={metric: metric_label},
            title=f"{metric_label} over time",
        )
        if metric == "clan_rank":
            fig.update_yaxes(autorange="reversed")  # rank 1 at the top
        st.plotly_chart(fig)

    with st.expander("Stored data for the selected players"):
        shown = pd.DataFrame(
            {
                "Time": data["Time"].dt.strftime("%Y-%m-%d %H:%M"),
                "Player": data["Player"],
                "Role": data["role"].map(ROLE_LABELS).fillna(data["role"]),
                "Town Hall": data["town_hall_level"].astype("Int64"),
                "XP Level": data["exp_level"].astype("Int64"),
                "Trophies": data["trophies"].astype("Int64"),
                "Builder Trophies": data["builder_base_trophies"].astype("Int64"),
                "Rank": data["clan_rank"].astype("Int64"),
                "Donated": data["donations"].astype("Int64"),
                "Received": data["donations_received"].astype("Int64"),
            }
        ).sort_values(["Player", "Time"])
        st.dataframe(shown, hide_index=True)


# ------------------------------------------------------------------ page

def render_members():
    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    st.caption(
        "Reads from the local SQLite database. Each click on the button below "
        "makes one API request and adds a new snapshot."
    )
    if fetch_and_save_snapshot(key="members_fetch"):
        st.session_state.pop("members_snapshot_id", None)  # jump to the new snapshot

    snapshots = get_snapshot_list(clan_tag)
    if not snapshots:
        st.info("No snapshots saved yet. Click **Fetch & Save Snapshot** above.")
        return

    ids = [s["id"] for s in snapshots]
    by_id = {s["id"]: s for s in snapshots}

    def label(snapshot_id):
        s = by_id[snapshot_id]
        text = f"#{snapshot_id} - {local_time(s['taken_at'])} - {s['member_count']} members"
        return text + " (latest)" if snapshot_id == ids[0] else text

    selected_id = st.selectbox("Snapshot", ids, format_func=label, key="members_snapshot_id")

    current = _to_frame(get_snapshot_members(selected_id))
    if current is None:
        st.warning("This snapshot contains no members.")
        return

    position = ids.index(selected_id)
    previous, previous_time = None, None
    if position + 1 < len(ids):
        previous = _to_frame(get_snapshot_members(ids[position + 1]))
        previous_time = local_time(by_id[ids[position + 1]]["taken_at"])

    snap = by_id[selected_id]
    st.caption(
        f"{snap['clan_name']} ({snap['clan_tag']}) - snapshot from "
        f"{local_time(snap['taken_at'])}"
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Members", len(current))
    m2.metric("Avg Town Hall", _avg(current["town_hall_level"], 1))
    m3.metric("Avg trophies", _avg(current["trophies"]))
    m4.metric("Total donated", _total(current["donations"]))
    m5.metric("Total received", _total(current["donations_received"]))

    tab_table, tab_charts, tab_history = st.tabs(
        ["Member table", "Charts", "Player history"]
    )
    with tab_table:
        _render_table_tab(selected_id, current, previous, previous_time)
    with tab_charts:
        _render_charts_tab(current)
    with tab_history:
        _render_history_tab(clan_tag)