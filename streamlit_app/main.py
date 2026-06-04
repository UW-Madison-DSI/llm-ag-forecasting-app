"""Top-level page composition: sidebar → three content tabs → footer."""

from __future__ import annotations

import streamlit as st

from features.config import DISEASE_OPTIONS

from streamlit_app.sidebar import sidebar_controls
from streamlit_app.tabs.biomass import render_biomass_forecast_tab
from streamlit_app.tabs.forecast import render_forecast_tab
from streamlit_app.tabs.trends import render_risk_trends_tab
from streamlit_app.tabs.weather import render_weather_tab
from streamlit_app.ui import render_feedback_banner, render_footer, render_print_context


def main() -> None:
    """Compose the whole dashboard."""
    selected_date, risk_days, disease_label = sidebar_controls()

    # Prominent feedback survey banner (screen only).
    render_feedback_banner()

    # Print-only context banner (hidden on screen; shown when an agent prints
    # or saves the view as a PDF via the sidebar button).
    render_print_context(selected_date, risk_days, disease_label)

    forecast_tab, trends_tab, weather_tab = st.tabs([
        "🌽 Disease Forecast",
        "📈 Risk Trends",
        "🌤 Weather Data",
    ])
    opts = DISEASE_OPTIONS[disease_label]
    with forecast_tab:
        if opts.get("type") == "biomass":
            render_biomass_forecast_tab(selected_date, opts.get("model_name", ""))
        else:
            render_forecast_tab(selected_date, risk_days, disease_label)
    with trends_tab:
        if opts.get("type") == "biomass":
            st.info("Risk Trends is only available for disease models. "
                    "Select a disease in the sidebar to use this tab.")
        else:
            render_risk_trends_tab(selected_date, disease_label)
    with weather_tab:
        render_weather_tab()

    render_footer()
