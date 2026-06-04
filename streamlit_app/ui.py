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
    """Page footer: project info + a dedicated Acknowledgements section."""
    st.markdown("---")
    st.markdown(
        f"""
<div style="font-size: 0.85rem; color: #6B7280; line-height: 1.6;">

  <!-- ---------- Project / data info ---------- -->
  <div style="margin-bottom: 0.4rem;">
    <strong>Source code:</strong>
    <a href="{SOURCE_CODE_URL}" target="_blank" rel="noopener"
       style="display: inline-flex; align-items: center; gap: 0.3rem;">
      <svg height="15" width="15" viewBox="0 0 16 16" fill="currentColor"
           aria-hidden="true" style="vertical-align: text-bottom;">
        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
          0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01
          1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95
          0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27
          2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82
          1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01
          2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
      </svg>
      {SOURCE_CODE_URL}
    </a>
  </div>
  <div style="margin-bottom: 0.4rem;">
    <strong>Disease-model references</strong> (Crop Protection Network encyclopedia):
    <a href="https://cropprotectionnetwork.org/encyclopedia/white-mold-of-soybean"
       target="_blank" rel="noopener">White mold (soybean)</a>,
    <a href="https://cropprotectionnetwork.org/encyclopedia/frogeye-leaf-spot-of-soybean"
       target="_blank" rel="noopener">Frogeye leaf spot (soybean)</a>,
    <a href="https://cropprotectionnetwork.org/encyclopedia/gray-leaf-spot-of-corn"
       target="_blank" rel="noopener">Gray leaf spot (corn)</a>,
    <a href="https://cropprotectionnetwork.org/encyclopedia/tar-spot-of-corn"
       target="_blank" rel="noopener">Tar spot (corn)</a>.
    <a href="https://cropsandsoils.extension.wisc.edu/articles/biomass-thresholds-for-cereal-rye-cover-crop-goals/"
       target="_blank" rel="noopener">Cereal Rye Biomass</a>.
  </div>
  <div style="margin-bottom: 0.4rem;">
    <em style="font-size: 0.78rem;">
      Forecast data sourced from the
      <a href="https://wisconet.wisc.edu/" target="_blank" rel="noopener">Wisconet</a>
      weather-station network.
    </em>
  </div>

  <!-- ---------- Acknowledgements ---------- -->
  <div style="margin-top: 0.9rem; padding-top: 0.6rem; border-top: 1px solid #E5E7EB;">
    <div style="font-weight: 700; color: #4B5563; text-transform: uppercase;
                letter-spacing: 0.4px; font-size: 0.8rem; margin-bottom: 0.35rem;">
      Acknowledgements
    </div>
    <ul style="margin: 0; padding-left: 1.1rem;">
      <li>
        Disease risk models developed by
        <a href="https://plantpath.wisc.edu/" target="_blank" rel="noopener">
          Dr. Damon Smith</a> and the Plant Pathology Department, UW–Madison.
      </li>
      <li>
        Dashboard scaffolding and Ag Forecasting API support by the
        <a href="https://datascience.wisc.edu/" target="_blank" rel="noopener">
          Data Science Institute, UW–Madison</a>.
      </li>
      <li>
        Open-source guidance from the
        <a href="https://ospo.wisc.edu/" target="_blank" rel="noopener">
          Open Source Program Office (OSPO), UW–Madison</a>, with special thanks
        to former Director of Open Source Allison Kittinger.
      </li>
      <li>
        Funded by a grant from the
        <a href="https://extension.wisc.edu/" target="_blank" rel="noopener">
          UW–Madison Division of Extension</a>, in keeping with the
        <a href="https://www.wisc.edu/wisconsin-idea/" target="_blank" rel="noopener">
          Wisconsin Idea</a>.
      </li>
      <li>
        Application authored by María Oros, Data Scientist at the Data Science
        Institute, UW–Madison (<code>moros2@wisc.edu</code>).
      </li>
    </ul>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
