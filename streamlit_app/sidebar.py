"""Sidebar controls — date, risk-days slider, disease dropdown, White Mold sub-radio."""

from __future__ import annotations

from datetime import date

import streamlit as st
import streamlit.components.v1 as components

from features.api import fetch_forecast
from features.config import DISEASE_OPTIONS

from streamlit_app.ui import SURVEY_URL


def _print_button() -> None:
    """Render a "Print / Save as PDF" button at the bottom of the sidebar.

    Streamlit's ``st.button`` can't run JavaScript, so this is a small
    same-origin component whose button calls ``window.parent.print()`` —
    printing the whole app document (not just the iframe). Combined with the
    ``@media print`` rules in ``page.py``, this lets an extension agent save a
    clean one-page handout of the current forecast for farmers without
    internet.
    """
    components.html(
        """
        <button onclick="window.parent.print()"
                style="width: 100%; padding: 0.5rem 0.75rem; cursor: pointer;
                       font-size: 0.95rem; font-weight: 600; color: #111827;
                       background: #ffffff; border: 1px solid rgba(17,24,39,0.25);
                       border-radius: 0.5rem; font-family: 'Source Sans Pro',
                       -apple-system, sans-serif;"
                onmouseover="this.style.borderColor='#C5050C'; this.style.color='#C5050C';"
                onmouseout="this.style.borderColor='rgba(17,24,39,0.25)'; this.style.color='#111827';">
            🖨️ Print / Save as PDF
        </button>
        """,
        height=48,
    )

# White-mold variants we collapse into one dropdown entry + sub-radio.
# Order matters: first key is the default radio choice.
WHITE_MOLD_VARIANTS = {
    "Non-irrigated":  "White Mold — Non-irrigated (soybean)",
    "Irrigated 30in": "White Mold — Irrigated 30in (soybean)",
    "Irrigated 15in": "White Mold — Irrigated 15in (soybean)",
}
WHITE_MOLD_LABEL = "White Mold (soybean)"



def build_visible_options(disease_options: dict | None = None) -> list[str]:
    """Build the dropdown labels with the three WM rows collapsed into one.

    Pure (no Streamlit), so tests can verify the menu order without a
    Streamlit runtime. ``disease_options`` defaults to the real config
    but can be overridden in tests.

    The collapsed ``WHITE_MOLD_LABEL`` is only inserted when at least
    one underlying variant is present in ``disease_options`` — so a
    config without any white-mold rows produces a clean menu.
    """
    opts = disease_options if disease_options is not None else DISEASE_OPTIONS
    variant_labels = set(WHITE_MOLD_VARIANTS.values())
    visible = [label for label in opts.keys() if label not in variant_labels]

    first_wm_idx = next(
        (i for i, label in enumerate(opts.keys()) if label in variant_labels),
        None,
    )
    if first_wm_idx is not None:
        visible.insert(first_wm_idx, WHITE_MOLD_LABEL)
    return visible


def resolve_disease_label(display_label: str, irrigation_key: str | None) -> str:
    """Map a (dropdown, sub-radio) selection back to a DISEASE_OPTIONS key.

    Pure helper for tests + sidebar_controls.
    """
    if display_label == WHITE_MOLD_LABEL:
        if irrigation_key not in WHITE_MOLD_VARIANTS:
            irrigation_key = next(iter(WHITE_MOLD_VARIANTS))
        return WHITE_MOLD_VARIANTS[irrigation_key]
    return display_label


def sidebar_controls() -> tuple[date, int, str]:
    """Render the sidebar and return the user's current selections.

    Returns:
        ``(selected_date, risk_days, disease_label)`` — ``disease_label``
        is a key into :data:`features.config.DISEASE_OPTIONS`.
    """
    visible_options = build_visible_options()

    with st.sidebar:
        st.header("Controls")
        selected_date = st.date_input(
            "Forecasting date",
            value=date.today(),
            max_value=date.today(),
        )
        display_label = st.selectbox("Disease model", visible_options)

        irrigation_key: str | None = None
        if display_label == WHITE_MOLD_LABEL:
            irrigation_key = st.radio(
                "Irrigation",
                options=list(WHITE_MOLD_VARIANTS.keys()),
                horizontal=False,
                help="White-mold risk depends on row spacing and irrigation; "
                     "choose the management scenario that matches the field.",
            )

        if st.button("🔄 Refresh data"):
            fetch_forecast.clear()
            st.rerun()

        # Risk-days slider lives at the bottom of the sidebar, below every
        # other control.
        st.divider()
        risk_days = st.slider("Risk days", min_value=1, max_value=7, value=1)

        # Export the current view as a printable / PDF handout.
        st.divider()
        st.caption("Export the current view as PDF:")
        _print_button()

        # Feedback survey — kept at the very bottom, unobtrusive.
        st.divider()
        st.link_button(
            "📝 We want your feedback",
            SURVEY_URL,
            use_container_width=True,
            help="Help us improve — takes just a couple of minutes.",
        )

    return selected_date, risk_days, resolve_disease_label(display_label, irrigation_key)
