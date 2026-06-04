"""Top-of-page chrome: ``st.set_page_config`` + UW logo + title.

Anything that has to run before the rest of the UI (and that touches
``st.set_page_config``, which Streamlit only allows once and only as the
first Streamlit call) lives here.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
LOGO_FULL = _ASSETS / "uw-logo-horizontal-color-web-digital.png"
LOGO_ICON = _ASSETS / "uw-logo-vertical-color-web-digital.png"


def configure_page() -> None:
    """Run all the must-be-first-thing page setup.

    Order matters: ``st.set_page_config`` first, then any ``st.logo`` /
    ``st.markdown`` calls for global styling, then the page title.
    """
    st.set_page_config(
        page_title="WI Agricultural Forecasting Advisory System",
        page_icon="🌽",
        layout="wide",
    )

    if LOGO_FULL.exists():
        st.logo(
            str(LOGO_FULL),
            size="large",
            icon_image=str(LOGO_ICON) if LOGO_ICON.exists() else None,
        )
        # st.logo caps at size="large"; bump it further via scoped CSS.
        # Scale the wide horizontal logo to the sidebar *width* (not a fixed
        # height) so it never overflows its container and gets clipped at the
        # edges. object-fit: contain keeps the aspect ratio intact.
        st.markdown(
            """
            <style>
                [data-testid="stSidebarHeader"] {
                    padding-top: 4rem;
                    padding-bottom: 1rem;
                    overflow: visible;
                }
                [data-testid="stSidebarHeader"] img,
                [data-testid="stLogo"] {
                    width: 100% !important;
                    max-width: 100% !important;
                    height: auto !important;
                    max-height: 90px !important;
                    object-fit: contain !important;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )

    st.title("🌽 WI Agricultural Forecasting Advisory System")
    st.caption(
        "Daily risk forecast from the UW–Madison Ag Forecasting API (Wisconet stations). "
        "Data is cached on disk for 24 h per (date, risk_days)."
    )
