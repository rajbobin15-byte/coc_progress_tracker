"""Helpers shared by the Wars, CWL, Clan Capital and Player Performance pages."""

import sqlite3
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from src.coc_api import ClashAPIError, ManualTokenRequired

STATE_LABELS = {
    "notInWar": "Not in war",
    "preparation": "Preparation day",
    "inWar": "Battle day",
    "warEnded": "War ended",
    "ended": "Ended",
    "ongoing": "Ongoing",
}
RESULT_LABELS = {"win": "Win", "lose": "Loss", "tie": "Tie"}


def when(iso):
    """Stored UTC timestamp -> local time string, or 'N/A'."""
    if not iso:
        return "N/A"
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def local_dt(iso):
    """Stored UTC timestamp -> naive local datetime for charts (NaT if missing)."""
    if not iso:
        return pd.NaT
    return datetime.fromisoformat(iso).astimezone().replace(tzinfo=None)


def is_past(iso):
    return bool(iso) and datetime.fromisoformat(iso) < datetime.now(timezone.utc)


def pct(value):
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.1f}%"


def num_cols(df, cols):
    """Convert columns to numbers in place (None -> NaN)."""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def int_cols(df, cols):
    """Convert columns to whole numbers where blanks stay blank."""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round().astype("Int64")
    return df


def render_token_recovery(exc, key):
    """
    Show a ClashAPIError. If it's ManualTokenRequired, also show the current
    outbound IP and a small form to paste in a fresh token - saving it to
    the database so it's used for every request from then on, no code or
    Render env var changes needed.
    """
    st.error(f"**{exc.title}**\n\n{exc}")
    if not isinstance(exc, ManualTokenRequired):
        return
    from src.token_manager import save_manual_token

    st.code(exc.current_ip, language=None)
    new_token = st.text_input(
        "Paste the new API token for this IP", type="password", key=f"{key}_new_token"
    )
    if st.button("Save token", key=f"{key}_save_token"):
        if new_token.strip():
            save_manual_token(new_token)
            st.success("Token saved. Retrying...")
            st.rerun()
        else:
            st.warning("Paste a token first.")


def fetch_button(label, key, work):
    """
    Show a button. On click run work(), which returns a list of
    (kind, message) where kind is success / info / warning / error, or
    "manual_token" with a ManualTokenRequired exception as the payload.
    API and database errors are shown clearly. Returns True if work finished.
    """
    if not st.button(label, key=key):
        return False
    try:
        with st.spinner("Contacting the Clash of Clans API..."):
            results = work()
    except ClashAPIError as exc:
        render_token_recovery(exc, key=key)
        return False
    except sqlite3.Error as exc:
        st.error(f"**Database error**\n\n{exc}")
        return False

    for kind, payload in results:
        if kind == "manual_token":
            render_token_recovery(payload, key=key)
        else:
            getattr(st, kind)(payload)
    return True


def show_raw(session_key, label="Raw API response from the last fetch (for troubleshooting)"):
    data = st.session_state.get(session_key)
    if data:
        with st.expander(label):
            st.json(data, expanded=False)
