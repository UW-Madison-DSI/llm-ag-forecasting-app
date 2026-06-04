"""Weather Data tab — multi-station, single-field time-series via wiscopy."""

from __future__ import annotations

from datetime import date, timedelta

import plotly.express as px
import streamlit as st

from features.config import WEATHER_DEFAULT_DAYS, WEATHER_FIELDS
from features.weather import fetch_weather_data, wiscopy_available


def render_weather_tab() -> None:
    """Render the Weather Data tab.

    Reads the station roster from ``st.session_state`` (populated by the
    Forecast tab). If wiscopy isn't installed, shows install instructions
    instead of crashing.
    """
    if not wiscopy_available():
        st.warning(
            "The `wiscopy` package is not installed. "
            "Install it (`pip install wiscopy`) and restart the app to enable this tab."
        )
        return

    station_options: dict[str, str] = st.session_state.get("station_options", {})
    if not station_options:
        st.info("Load the Forecast tab first so the station roster is available.")
        return

    # Display label → wiscopy station id (the forecast API's 4-char code).
    label_to_wid = {
        f"{name} ({sid})": sid for sid, name in station_options.items()
    }
    default_labels = list(label_to_wid.keys())[:2]

    col_l, col_r = st.columns([3, 2])
    with col_l:
        selected_labels = st.multiselect(
            "Stations",
            options=list(label_to_wid.keys()),
            default=default_labels,
        )
    with col_r:
        default_end = date.today()
        default_start = default_end - timedelta(days=WEATHER_DEFAULT_DAYS)
        date_range = st.date_input(
            "Date range",
            value=(default_start, default_end),
            max_value=date.today(),
        )

    # `field` is the wiscopy field name (passed through to the API);
    # `WEATHER_FIELDS[field]` is the human-readable label shown to the user.
    field = st.selectbox(
        "Weather field",
        options=list(WEATHER_FIELDS),
        format_func=WEATHER_FIELDS.__getitem__,
        index=0,
    )

    if not selected_labels:
        st.info("Pick at least one station above.")
        return
    if not isinstance(date_range, tuple) or len(date_range) != 2:
        st.info("Pick a start and end date.")
        return

    start, end = date_range
    wisco_ids = tuple(label_to_wid[label] for label in selected_labels)

    try:
        df = fetch_weather_data(wisco_ids, start.isoformat(), end.isoformat(), (field,))
    except Exception as err:  # wiscopy raises various; treat all as recoverable
        st.error(f"Could not fetch weather data: {err}")
        return

    if df is None or df.empty:
        st.warning("No observations returned for these inputs.")
        return

    units = df["final_units"].iloc[0] if "final_units" in df.columns else ""
    label = WEATHER_FIELDS.get(field, field)
    title = f"{label} ({units})" if units else label

    plot_df = df.reset_index()
    time_col = plot_df.columns[0]
    fig = px.line(
        plot_df,
        x=time_col,
        y="value",
        color="station_id" if "station_id" in plot_df.columns else None,
        title=title,
        labels={"value": units or "value", time_col: "time"},
    )
    fig.update_layout(height=520, margin={"r": 0, "t": 50, "l": 0, "b": 0})
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Raw data"):
        st.dataframe(df, use_container_width=True)
