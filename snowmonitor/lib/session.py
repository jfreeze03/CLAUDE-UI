"""Snowflake session + cached, guarded query runner.

Works both inside Streamlit-in-Snowflake (native active session) and on Streamlit
Community Cloud (st.connection from secrets). Every query is tagged, time-bounded,
cached by tier, and returns an empty DataFrame on error instead of crashing a page.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import config

# Cache TTLs (seconds) by tier.
_TTL = {"live": 30, "standard": 300, "historical": 3600, "metadata": 14400}
_STATEMENT_TIMEOUT_SECONDS = 300
_QUERY_TAG = f"{config.APP_NAME}"


def _new_session():
    """Create a Snowpark session, preferring the SiS native active session."""
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        pass
    # Community Cloud / local: use a configured Streamlit connection.
    try:
        return st.connection("snowflake").session()
    except Exception as exc:
        st.error(
            "No Snowflake connection. Deploy inside Snowflake (Streamlit-in-Snowflake) "
            "or configure a [connections.snowflake] secret."
        )
        raise exc


def get_session():
    """Return a cached Snowpark session, applying session parameters once."""
    if "sf_session" not in st.session_state:
        sess = _new_session()
        try:
            sess.sql(
                "ALTER SESSION SET "
                f"QUERY_TAG = '{_QUERY_TAG}', "
                f"STATEMENT_TIMEOUT_IN_SECONDS = {_STATEMENT_TIMEOUT_SECONDS}, "
                "TIMEZONE = 'UTC'"
            ).collect()
        except Exception:
            pass
        st.session_state["sf_session"] = sess
    return st.session_state["sf_session"]


def _execute(sql: str) -> pd.DataFrame:
    df = get_session().sql(sql).to_pandas()
    # Normalize column names to UPPER for consistent downstream access.
    df.columns = [str(c).upper() for c in df.columns]
    return df


@st.cache_data(ttl=_TTL["live"], show_spinner=False)
def _q_live(sql: str, _salt: str = "") -> pd.DataFrame:
    return _execute(sql)


@st.cache_data(ttl=_TTL["standard"], show_spinner=False)
def _q_standard(sql: str, _salt: str = "") -> pd.DataFrame:
    return _execute(sql)


@st.cache_data(ttl=_TTL["historical"], show_spinner=False)
def _q_historical(sql: str, _salt: str = "") -> pd.DataFrame:
    return _execute(sql)


@st.cache_data(ttl=_TTL["metadata"], show_spinner=False)
def _q_metadata(sql: str, _salt: str = "") -> pd.DataFrame:
    return _execute(sql)


_TIERS = {"live": _q_live, "standard": _q_standard, "historical": _q_historical, "metadata": _q_metadata}


def run(sql: str, tier: str = "standard", salt: str = "") -> pd.DataFrame:
    """Run a query through the tiered cache. Returns empty DataFrame on error."""
    fn = _TIERS.get(tier, _q_standard)
    try:
        return fn(sql, salt)
    except Exception as exc:
        st.warning(f"Query failed ({tier}): {str(exc)[:240]}")
        return pd.DataFrame()


def refresh_salt() -> str:
    """A salt that changes on explicit refresh to bypass the cache."""
    return str(st.session_state.get("_refresh_salt", ""))


def bump_refresh() -> None:
    import time
    st.session_state["_refresh_salt"] = str(time.time())
