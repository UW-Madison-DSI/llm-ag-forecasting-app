"""Risk Trends tab — N-day per-station risk time-series + class-distribution bar."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from features.api import fetch_forecast
from features.config import CLASS_COLORS, DISEASE_OPTIONS, RISK_TRENDS_DAYS
from features.data import flatten_features

from streamlit_app.llm import render_trends_chat_expander


def render_risk_trends_tab(selected_date, disease_label: str) -> None:
    """Render the Risk Trends tab: ``RISK_TRENDS_DAYS``-day risk time-series."""
    opts = DISEASE_OPTIONS[disease_label]
    risk_field  = opts["risk_field"]
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

    # Prefer the inner "forecasting_date" (the day being predicted); fall
    # back to the outer "date" if missing.
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

    # Companion: stacked bar of risk-class counts per day.
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

    render_trends_chat_expander(
        selected_date.isoformat(), disease_label, df, risk_field, class_field,
    )
