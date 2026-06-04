"""Disease Forecast tab — metric tiles, station map, model info, data table."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import requests
import streamlit as st

from features.api import fetch_forecast, fetch_model_info
from features.config import CLASS_COLORS, DISEASE_OPTIONS
from features.data import flatten_features, prepare_disease_df
from features.map_view import build_map

from streamlit_app.llm import render_chat_expander, render_model_explain_button
from streamlit_app.ui import NEUTRAL_TILE_COLOR, color_tile


# ---------------------------------------------------------------------------
# Counting helpers — pure functions, tested in tests/test_metrics_counts.py
# ---------------------------------------------------------------------------

def count_risk_buckets(risk_class_series: pd.Series) -> dict[str, int]:
    """Tally High / Moderate / Low / Active counts from a ``risk_class`` series.

    Robust to inconsistent casing and to numeric-prefixed values like
    ``"1.Low"`` (the API sometimes attaches a sort key during active
    seasons). "Active" excludes Inactive / Unknown / None / N/A.
    """
    rc = risk_class_series.astype(str).fillna("")
    inactive = rc.str.contains(r"inactive|unknown|none|n/?a", case=False, regex=True, na=False)
    return {
        "total":    int(len(rc)),
        "active":   int((~inactive).sum()),
        "high":     int(rc.str.contains(r"\bhigh\b", case=False, regex=True, na=False).sum()),
        "moderate": int(rc.str.contains(r"\bmoderate\b|\bmedium\b", case=False, regex=True, na=False).sum()),
        "low":      int(rc.str.contains(r"\blow\b", case=False, regex=True, na=False).sum()),
    }


# ---------------------------------------------------------------------------
# UI fragments
# ---------------------------------------------------------------------------

def show_metrics(df: pd.DataFrame) -> None:
    """Render the five summary metric tiles above the map."""
    if "risk_class" not in df.columns:
        st.warning("`risk_class` column is missing — counts unavailable.")
        return

    counts = count_risk_buckets(df["risk_class"])

    cols = st.columns(5)
    color_tile(cols[0], NEUTRAL_TILE_COLOR, "Total stations", counts["total"])
    color_tile(
        cols[1], NEUTRAL_TILE_COLOR, "Active stations", counts["active"],
        tooltip="Stations where the selected model is currently running (not Inactive).",
    )
    color_tile(cols[2], CLASS_COLORS["High"],     "High risk",     counts["high"])
    color_tile(cols[3], CLASS_COLORS["Moderate"], "Moderate risk", counts["moderate"])
    color_tile(cols[4], CLASS_COLORS["Low"],      "Low risk",      counts["low"])

    # Surface the raw class values so it's easy to see if a model is using
    # unexpected labels.
    rc = df["risk_class"].astype(str).fillna("")
    with st.expander("🔍 Risk-class values seen", expanded=False):
        table = (
            rc.value_counts(dropna=False)
              .rename_axis("risk_class")
              .reset_index(name="stations")
        )
        st.dataframe(table, use_container_width=True, hide_index=True)


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
    """Render an "About this model" expander with metadata from the API."""
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

        if info.get("description"):
            st.markdown(info["description"])

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

        render_model_explain_button(disease_label, model_name, info)


# ---------------------------------------------------------------------------
# Tab entrypoint
# ---------------------------------------------------------------------------

def render_forecast_tab(selected_date, risk_days: int, disease_label: str) -> None:
    """Render the Disease Forecast tab.

    Also stashes the (station_id → station_name) mapping in
    ``st.session_state`` so the Weather and Risk Trends tabs can
    populate their pickers without re-fetching.
    """
    opts = DISEASE_OPTIONS[disease_label]
    risk_field  = opts["risk_field"]
    class_field = opts["class_field"]
    model_name  = opts["model_name"]

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

    st.session_state["station_options"] = dict(
        zip(df["station_id"].astype(str), df["station_name"].astype(str))
    )

    map_df = prepare_disease_df(df, risk_field, class_field)
    show_metrics(map_df)
    st.plotly_chart(build_map(map_df, disease_label), use_container_width=True)
    show_model_info(model_name, disease_label)
    show_table(map_df, risk_field, class_field)
    render_chat_expander(
        selected_date.isoformat(), disease_label, map_df, risk_field, class_field,
    )

    st.caption(f"Last loaded: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
