"""Wisconet weather time-series fetching via the ``wiscopy`` client.

``wiscopy`` is treated as an optional dependency so the rest of the
app keeps working even if it isn't installed. Use
:func:`wiscopy_available` to guard UI before calling the fetcher.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def wiscopy_available() -> bool:
    """True if the optional ``wiscopy`` package is importable."""
    try:
        import wiscopy.interface  # noqa: F401
    except ImportError:
        return False
    return True


@st.cache_resource(show_spinner=False)
def _get_client():
    """Singleton ``Wisconet`` client, cached per Streamlit process.

    Uses ``cache_resource`` (not ``cache_data``) because the client
    holds a live connection / config — it isn't meant to be serialized.
    """
    from wiscopy.interface import Wisconet  # lazy import
    return Wisconet()


@st.cache_data(ttl=3_600, show_spinner="Fetching weather data…")
def fetch_weather_data(
    station_ids: tuple[str, ...],
    start_time: str,
    end_time: str,
    fields: tuple[str, ...],
) -> pd.DataFrame:
    """Fetch weather observations from Wisconet via wiscopy.

    Cached for 1 h keyed by ``(station_ids, start_time, end_time, fields)``.
    Args must be tuples (hashable) so Streamlit can key the cache.

    Args:
        station_ids: Lowercase station ids (e.g. ``("maple", "arlington")``).
        start_time: ISO date string ``"YYYY-MM-DD"``.
        end_time: ISO date string ``"YYYY-MM-DD"``.
        fields: Wisconet field names (see ``config.WEATHER_FIELDS``).

    Returns:
        Long-format DataFrame in wiscopy's native shape — typically
        ``value``, ``station_id``, ``final_units`` columns with the
        timestamp on the index.
    """
    client = _get_client()
    return client.get_data(
        station_ids=list(station_ids),
        start_time=start_time,
        end_time=end_time,
        fields=list(fields),
    )
