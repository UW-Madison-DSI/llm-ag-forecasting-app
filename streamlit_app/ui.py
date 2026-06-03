"""Small reusable UI helpers — metric tiles, page footer.

Pure presentation. No data access, no API calls.
"""

from __future__ import annotations

import streamlit as st

SOURCE_CODE_URL = "https://github.com/UW-Madison-DSI/ag_forecasting_app_v3"
NEUTRAL_TILE_COLOR = "#34495e"


def color_tile(col, color: str, label: str, value, tooltip: str = "") -> None:
    """Render one metric tile with a colored label.

    Uses raw HTML so the label color can match :data:`CLASS_COLORS`
    exactly (Streamlit's ``st.metric`` only supports a fixed palette).
    """
    tip_attr = f' title="{tooltip}"' if tooltip else ""
    col.markdown(
        f"""
        <div{tip_attr} style="line-height: 1.2;">
            <div style="color: {color}; font-size: 0.85rem; font-weight: 700;
                        text-transform: uppercase; letter-spacing: 0.4px;">
                {label}
            </div>
            <div style="font-size: 2rem; font-weight: 700; margin-top: 4px;">
                {value}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    """Page footer: source-code link + acknowledgments."""
    st.markdown("---")
    st.markdown(
        f"""
<div style="font-size: 0.85rem; color: #6B7280; line-height: 1.6;">
  <strong>Source code:</strong>
  <a href="{SOURCE_CODE_URL}" target="_blank" rel="noopener">
    {SOURCE_CODE_URL}
  </a>
  <br>
  <strong>Acknowledgments:</strong>
  Disease risk models developed by
  <a href="https://plantpath.wisc.edu/" target="_blank" rel="noopener">
    Dr. Damon Smith</a> and the Plant Pathology Department at UW–Madison.
  Dashboard scaffolding and Ag Forecasting API support by the
  <a href="https://datascience.wisc.edu/" target="_blank" rel="noopener">
    Data Science Institute, University of Wisconsin–Madison</a>.
  Application authored by María Oros (<code>moros2@wisc.edu</code>).
  <br>
  <em style="font-size: 0.78rem;">
    Forecast data sourced from the
    <a href="https://wisconet.wisc.edu/" target="_blank" rel="noopener">Wisconet</a>
    weather-station network.
  </em>
</div>
        """,
        unsafe_allow_html=True,
    )
