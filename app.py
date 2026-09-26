import pandas as pd
import streamlit as st

from src.analytics_db import init_analytics_db
from src.capital_page import render_capital
from src.coc_api import ClashAPIError, get_clan_info
from src.cwl_page import render_cwl
from src.database import get_latest_snapshot, get_snapshot_count, init_db
from src.members_page import render_members
from src.performance_page import render_performance
from src.ui_helpers import (
    ROLE_LABELS,
    current_clan_tag,
    fetch_and_save_snapshot,
    fmt,
    local_time,
)
from src.analytics_ui import render_token_recovery
from src.wars_page import render_wars

st.set_page_config(
    page_title="Clash of Clans Clan Analytics",
    page_icon="⚔️",
    layout="wide",
)

init_db()            # existing snapshot tables (unchanged)
init_analytics_db()  # new war / CWL / capital tables (added safely)

PAGES = [
    "Dashboard",
    "Members",
    "Wars",
    "CWL",
    "Clan Capital",
    "Player Performance",
]


# ------------------------------------------------------------ Dashboard

def render_api_test():
    st.subheader("Test API Connection")
    st.caption("Uses CLAN_TAG and COC_API_TOKEN from the local .env file.")

    if st.button("Test API Connection"):
        st.session_state.pop("clan_data", None)
        with st.spinner("Contacting the Clash of Clans API..."):
            try:
                st.session_state["clan_data"] = get_clan_info()
            except ClashAPIError as exc:
                render_token_recovery(exc, key="dashboard_test")

    clan = st.session_state.get("clan_data")
    if not clan:
        return

    st.success("Connection successful.")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Clan name", fmt(clan.get("name")))
    col2.metric("Clan tag", fmt(clan.get("tag")))
    col3.metric("Clan level", fmt(clan.get("clanLevel")))
    col4.metric("Members", fmt(clan.get("members")))

    col5, col6, col7 = st.columns(3)
    col5.metric("Clan points (trophies)", fmt(clan.get("clanPoints")))
    col6.metric("Builder Base points", fmt(clan.get("clanBuilderBasePoints")))
    col7.metric("Capital points", fmt(clan.get("clanCapitalPoints")))

    st.subheader("Current members")
    rows = [
        {
            "Player name": member.get("name"),
            "Player tag": member.get("tag"),
            "Town Hall": member.get("townHallLevel"),
            "Role": ROLE_LABELS.get(member.get("role"), member.get("role")),
            "Trophies": member.get("trophies"),
        }
        for member in clan.get("memberList", [])
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_snapshot_section():
    st.divider()
    st.subheader("Data collection")
    st.caption(
        "Fetches the clan from the API and saves a timestamped snapshot to the "
        "local SQLite database (data/clash.db). Every click adds a new snapshot. "
        "View the saved data on the Members page."
    )

    clan_tag = current_clan_tag()
    if clan_tag is None:
        return

    fetch_and_save_snapshot(key="dashboard_fetch")

    count = get_snapshot_count(clan_tag)
    latest = get_latest_snapshot(clan_tag)
    col1, col2 = st.columns(2)
    col1.metric("Snapshots stored", fmt(count))
    col2.metric("Latest snapshot", local_time(latest["taken_at"]) if latest else "None yet")


# ------------------------------------------------------------------ main

st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", PAGES, label_visibility="collapsed")

st.title("Clash of Clans Clan Analytics")
st.header(page)

if page == "Dashboard":
    render_api_test()
    render_snapshot_section()
elif page == "Members":
    render_members()
elif page == "Wars":
    render_wars()
elif page == "CWL":
    render_cwl()
elif page == "Clan Capital":
    render_capital()
elif page == "Player Performance":
    render_performance()