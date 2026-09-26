"""Small helpers shared by the Streamlit pages."""

import sqlite3
from datetime import datetime

import streamlit as st

from src.coc_api import ClashAPIError, get_clan_info, load_config
from src.database import save_snapshot

ROLE_LABELS = {
    "leader": "Leader",
    "coLeader": "Co-Leader",
    "admin": "Elder",
    "member": "Member",
}


def fmt(value):
    """Format a value for display, showing N/A when there is no value."""
    if value is None:
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def local_time(iso_utc):
    """Convert a stored UTC timestamp to this PC's local time for display."""
    return datetime.fromisoformat(iso_utc).astimezone().strftime("%Y-%m-%d %H:%M")


def current_clan_tag():
    """The configured clan tag, or None (after showing an error) if .env is bad."""
    try:
        return load_config()[1]
    except ClashAPIError as exc:
        st.error(f"**{exc.title}**\n\n{exc}")
        return None


def fetch_and_save_snapshot(key):
    """
    Show the 'Fetch & Save Snapshot' button. On click, fetch the clan from the
    API and store a snapshot. Returns the new snapshot id, or None.
    """
    if not st.button("Fetch & Save Snapshot", key=key):
        return None

    try:
        with st.spinner("Fetching clan data and saving snapshot..."):
            clan = get_clan_info()
            snapshot_id = save_snapshot(clan)
    except ClashAPIError as exc:
        st.error(f"**{exc.title}**\n\n{exc}")
        return None
    except sqlite3.Error as exc:
        st.error(f"**Database error**\n\n{exc}")
        return None

    st.success(
        f"Snapshot #{snapshot_id} saved with "
        f"{len(clan.get('memberList', []))} members."
    )
    return snapshot_id