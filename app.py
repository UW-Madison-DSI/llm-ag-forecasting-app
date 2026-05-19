"""Streamlit entrypoint for the WI crop disease risk dashboard.

Composes the sidebar controls, metric tiles, station map, data table,
and weather time-series from the building blocks in the ``features``
package. Run with::

    streamlit run app.py
"""

from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from features.api import fetch_forecast, fetch_model_info
from features.config import (
    CLASS_COLORS,
    DISEASE_OPTIONS,
    RISK_TRENDS_DAYS,
    WEATHER_DEFAULT_DAYS,
    WEATHER_FIELDS,
)
from features.data import flatten_features, prepare_disease_df
from features.map_view import build_map
from features.weather import fetch_weather_data, wiscopy_available


st.set_page_config(
    page_title="WI Crop Disease Risk Forecast",
    page_icon="🌽",
    layout="wide",
)

st.title("🌽 Wisconsin Crop Disease Risk Forecast")
st.caption(
    "Daily risk forecast from the UW–Madison Ag Forecasting API (Wisconet stations). "
    "Data is cached on disk for 24 h per (date, risk_days)."
)


def sidebar_controls() -> tuple[date, int, str]:
    """Render the sidebar and return the user's current selections.

    Returns:
        ``(selected_date, risk_days, disease_label)`` chosen by the user.
        ``disease_label`` is a key into :data:`DISEASE_OPTIONS`.
    """
    with st.sidebar:
        st.header("Controls")
        selected_date = st.date_input(
            "Forecasting date",
            value=date.today() - timedelta(days=1),
            max_value=date.today(),
        )
        risk_days = st.slider("Risk days", min_value=1, max_value=7, value=1)
        disease_label = st.selectbox("Disease model", list(DISEASE_OPTIONS.keys()))
        if st.button("🔄 Refresh data"):
            fetch_forecast.clear()
            st.rerun()
    return selected_date, risk_days, disease_label


def show_metrics(df: pd.DataFrame) -> None:
    """Render the four summary metric tiles above the map."""
    active = df[~df["risk_class"].isin(["Inactive", "Unknown"])]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Stations", len(df))
    col2.metric("Active models", len(active))
    col3.metric("High risk", int((df["risk_class"] == "High").sum()))
    col4.metric("Moderate risk", int((df["risk_class"] == "Moderate").sum()))


def show_table(df: pd.DataFrame, risk_field: str, class_field: str) -> None:
    """Render the collapsible per-station data table."""
    with st.expander("Station data table"):
        cols = [
            "station_id", "station_name", "city", "county", "region",
            "latitude", "longitude", risk_field, class_field, "forecasting_date",
        ]
        cols = [c for c in cols if c in df.columns]
        st.dataframe(df[cols].sort_values(class_field), use_container_width=True)


def show_model_info(model_name: str, disease_label: str) -> None:
    """Render an "About this model" expander with metadata from the API.

    Pulls description, input variables, model type, risk-output scale,
    inactive rule, and version from the ``/models/{model_name}`` endpoint.
    Falls back silently if the lookup fails (e.g. unknown model name).
    """
    info = fetch_model_info(model_name)
    with st.expander(f"📖 About this model — {disease_label}", expanded=False):
        if not info:
            st.info(
                f"No metadata available for model `{model_name}`. "
                "Check the model name in `features/config.py`."
            )
            return

        name = info.get("name", model_name)
        crop = info.get("crop")
        version = info.get("version")
        header = f"**{name}**"
        if crop:
            header += f"  ·  crop: *{crop}*"
        if version:
            header += f"  ·  v{version}"
        st.markdown(header)

        description = info.get("description")
        if description:
            st.markdown(description)

        col1, col2 = st.columns(2)
        with col1:
            if info.get("model_type"):
                st.markdown(f"**Model type:** {info['model_type']}")
            if info.get("risk_output"):
                st.markdown(f"**Risk output:** {info['risk_output']}")
        with col2:
            if info.get("inactive_rule"):
                st.markdown(f"**Inactive rule:** {info['inactive_rule']}")

        variables = info.get("variables") or []
        if variables:
            st.markdown("**Input variables**")
            st.markdown("\n".join(f"- `{v}`" for v in variables))


def render_forecast_tab(selected_date: date, risk_days: int, disease_label: str) -> None:
    """Render the Disease Forecast tab: metrics, map, model info, table.

    Also stashes the (station_id → station_name) mapping in
    ``st.session_state`` so the Weather tab can populate its picker
    without re-fetching.
    """
    opts = DISEASE_OPTIONS[disease_label]
    risk_field = opts["risk_field"]
    class_field = opts["class_field"]
    model_name = opts["model_name"]

    try:
        payload = fetch_forecast(selected_date.isoformat(), risk_days)
    except requests.HTTPError as err:
        st.error(f"API returned an error: {err.response.status_code} — {err.response.text[:200]}")
        return
    except requests.RequestException as err:
        st.error(f"Could not reach the forecasting API: {err}")
        return

    df = flatten_features(payload)
    if df.empty:
        st.warning("No station data returned for this date.")
        return

    # Share station roster with the Weather tab via session_state.
    st.session_state["station_options"] = dict(
        zip(df["station_id"].astype(str), df["station_name"].astype(str))
    )

    map_df = prepare_disease_df(df, risk_field, class_field)
    show_metrics(map_df)
    st.plotly_chart(build_map(map_df, disease_label), use_container_width=True)
    show_model_info(model_name, disease_label)
    show_table(map_df, risk_field, class_field)

    st.caption(f"Last loaded: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def render_weather_tab() -> None:
    """Render the Weather Data tab: time-series of one field per station.

    Reads the station roster from ``st.session_state`` (populated by
    the Forecast tab). If wiscopy isn't installed, shows install
    instructions instead of crashing.
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

    # Map display label → wiscopy station id (lowercased station name,
    # matching the wiscopy convention from your example).
    label_to_wid = {
        f"{name} ({sid})": name.lower() for sid, name in station_options.items()
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

    field = st.selectbox("Weather field", options=WEATHER_FIELDS, index=0)

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
    title = f"{field} ({units})" if units else field

    # Plotly needs the time on a column, not the index.
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


def render_risk_trends_tab(selected_date: date, disease_label: str) -> None:
    """Render the Risk Trends tab: N-day risk time-series per station.

    Always fetches with ``risk_days=RISK_TRENDS_DAYS`` (independent of
    the sidebar slider, which controls the Forecast tab) so this view
    is consistently a multi-day trend chart.

    The API returns one timeseries entry per forecast day. We unstack
    those into long format and plot risk-over-time, one line per
    selected station. Sentinel ``-1`` values (model inactive) are
    converted to gaps so they don't drag the line below zero.
    """
    opts = DISEASE_OPTIONS[disease_label]
    risk_field = opts["risk_field"]
    class_field = opts["class_field"]

    station_options: dict[str, str] = st.session_state.get("station_options", {})
    if not station_options:
        st.info("Load the Forecast tab first so the station roster is available.")
        return

    label_to_sid = {f"{name} ({sid})": sid for sid, name in station_options.items()}
    default_labels = list(label_to_sid.keys())[:5]
    selected_labels = st.multiselect(
        "Stations",
        options=list(label_to_sid.keys()),
        default=default_labels,
        help="Compare up to a handful of stations to see how risk evolves.",
    )

    if not selected_labels:
        st.info("Pick at least one station above.")
        return

    selected_sids = {label_to_sid[label] for label in selected_labels}

    try:
        payload = fetch_forecast(selected_date.isoformat(), RISK_TRENDS_DAYS)
    except requests.HTTPError as err:
        st.error(f"API returned an error: {err.response.status_code} — {err.response.text[:200]}")
        return
    except requests.RequestException as err:
        st.error(f"Could not reach the forecasting API: {err}")
        return

    df = flatten_features(payload)
    if df.empty:
        st.warning("No data returned for this date.")
        return

    df = df[df["station_id"].astype(str).isin(selected_sids)].copy()
    if df.empty:
        st.warning("No data for the selected stations.")
        return

    # Prefer the inner "forecasting_date" (the day being predicted);
    # fall back to the outer timeseries "date" if missing.
    date_col = "forecasting_date" if "forecasting_date" in df.columns else "date"
    df["plot_date"] = pd.to_datetime(df[date_col], errors="coerce")
    df[risk_field] = pd.to_numeric(df[risk_field], errors="coerce")
    # -1 marks "model inactive" — show as a gap, not a dip.
    df["risk_plot"] = df[risk_field].where(df[risk_field] != -1)

    df = df.sort_values(["station_name", "plot_date"])

    fig = px.line(
        df,
        x="plot_date",
        y="risk_plot",
        color="station_name",
        markers=True,
        title=f"{disease_label} — {RISK_TRENDS_DAYS}-day risk forecast",
        labels={"plot_date": "Forecasting date", "risk_plot": "Risk", "station_name": "Station"},
    )
    fig.update_layout(height=520, margin={"r": 0, "t": 50, "l": 0, "b": 0})
    fig.update_traces(connectgaps=False)
    st.plotly_chart(fig, use_container_width=True)

    # Companion: stacked bar of risk-class counts per day, so you can
    # see how many stations cross into High/Moderate each day.
    if class_field in df.columns:
        class_counts = (
            df.assign(risk_class=df[class_field].astype(str).str.title())
            .groupby(["plot_date", "risk_class"])
            .size()
            .reset_index(name="stations")
        )
        present = [c for c in CLASS_COLORS if c in class_counts["risk_class"].unique()]
        fig_bar = px.bar(
            class_counts,
            x="plot_date",
            y="stations",
            color="risk_class",
            color_discrete_map=CLASS_COLORS,
            category_orders={"risk_class": present},
            title="Risk class distribution across selected stations",
            labels={"plot_date": "Forecasting date", "stations": "Stations"},
        )
        fig_bar.update_layout(height=360, margin={"r": 0, "t": 50, "l": 0, "b": 0})
        st.plotly_chart(fig_bar, use_container_width=True)

    with st.expander("Raw data"):
        cols = ["station_id", "station_name", "plot_date", risk_field, class_field]
        cols = [c for c in cols if c in df.columns]
        st.dataframe(df[cols], use_container_width=True)


def main() -> None:
    """Top-level page composition: sidebar → three content tabs."""
    selected_date, risk_days, disease_label = sidebar_controls()

    forecast_tab, trends_tab, weather_tab = st.tabs([
        "🌽 Disease Forecast",
        "📈 Risk Trends",
        "🌤 Weather Data",
    ])
    with forecast_tab:
        render_forecast_tab(selected_date, risk_days, disease_label)
    with trends_tab:
        render_risk_trends_tab(selected_date, disease_label)
    with weather_tab:
        render_weather_tab()


if __name__ == "__main__":
    main()
