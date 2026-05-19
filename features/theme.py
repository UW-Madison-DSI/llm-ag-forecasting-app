"""Front-end design system for the dashboard.

This is the **single place** to change the look-and-feel of the app
(brand colors, typography, spacing, component skins). It complements
``.streamlit/config.toml``, which controls Streamlit's native widgets,
by injecting scoped CSS for everything the native theme can't reach:

- tab pill styling and active-state highlight
- expander headers
- the custom metric tiles rendered by ``app.show_metrics``
- section dividers and content padding

Usage in ``app.py``:

    from features.theme import BRAND, inject_custom_css

    st.set_page_config(...)
    inject_custom_css()      # ← do this once, right after set_page_config
    st.title(...)
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Brand tokens.  Edit these to retheme the app.
# ---------------------------------------------------------------------------

BRAND = {
    # UW–Madison palette (matches .streamlit/config.toml).
    "primary":       "#C5050C",   # Badger Red
    "primary_dark":  "#9B0000",   # Anchor (hover / pressed states)
    "primary_soft":  "#FCEAEC",   # tinted background for callouts

    # Neutrals.
    "ink":           "#111827",   # body text
    "ink_muted":     "#6B7280",   # secondary text / captions
    "surface":       "#FFFFFF",   # cards / tiles
    "surface_alt":   "#F4F4F5",   # page background-ish

    # Borders / dividers.
    "border":        "rgba(17,24,39,0.12)",
    "border_strong": "rgba(17,24,39,0.25)",

    # Typography.
    "font_stack":    "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif",

    # Spacing scale (px).  Reuse instead of hardcoding magic numbers.
    "radius":        "8px",
    "tile_pad":      "12px 16px",
}


# Risk-class colors used by the map markers and the metric tile labels.
# Re-exported here so theme tweaks can flow through one source of truth.
# (Keeps backward compat with features.config.CLASS_COLORS.)
RISK_COLORS = {
    "High":     "#E74C3C",
    "Moderate": "#F39C12",
    "Low":      "#2ECC71",
    "Inactive": "#95A5A6",
    "Unknown":  "#BDC3C7",
}


# ---------------------------------------------------------------------------
# CSS injection.  All selectors below are scoped to Streamlit's own DOM
# (data-testid attributes), so we don't accidentally clobber third-party
# widgets or future Streamlit updates.
# ---------------------------------------------------------------------------

def _css() -> str:
    b = BRAND
    return f"""
    <style>
      /* Page-level typography. */
      html, body, [class*="css"]  {{
        font-family: {b['font_stack']};
        color: {b['ink']};
      }}

      /* Tighten the top padding so the title sits closer to the logo. */
      .block-container {{
        padding-top: 1.2rem;
        padding-bottom: 2rem;
      }}

      /* H1 — branded accent bar to the left of the page title. */
      h1 {{
        border-left: 4px solid {b['primary']};
        padding-left: 12px;
        margin-bottom: 0.25rem;
      }}

      /* Tabs: more prominent active state. */
      div[data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 1px solid {b['border']};
      }}
      button[data-baseweb="tab"] {{
        font-weight: 600;
        color: {b['ink_muted']};
        padding: 10px 16px;
      }}
      button[data-baseweb="tab"][aria-selected="true"] {{
        color: {b['primary']};
        border-bottom: 2px solid {b['primary']} !important;
      }}

      /* Expander headers — quieter, with a thin left accent. */
      details > summary,
      div[data-testid="stExpander"] summary {{
        font-weight: 600;
        color: {b['ink']};
      }}
      div[data-testid="stExpander"] {{
        border: 1px solid {b['border']};
        border-radius: {b['radius']};
        background: {b['surface']};
      }}

      /* Native st.metric tiles: a touch more padding + subtle background. */
      div[data-testid="stMetric"] {{
        background: {b['surface']};
        border: 1px solid {b['border']};
        border-radius: {b['radius']};
        padding: {b['tile_pad']};
      }}
      div[data-testid="stMetricLabel"] {{
        color: {b['ink_muted']};
        font-weight: 600;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.4px;
      }}
      div[data-testid="stMetricValue"] {{
        color: {b['ink']};
        font-weight: 700;
      }}

      /* Sidebar — slightly tinted background so it visually separates. */
      section[data-testid="stSidebar"] {{
        background: {b['surface_alt']};
        border-right: 1px solid {b['border']};
      }}
      section[data-testid="stSidebar"] h2 {{
        color: {b['primary_dark']};
        font-size: 1.05rem;
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }}

      /* Caption / footer text. */
      [data-testid="stCaptionContainer"] {{
        color: {b['ink_muted']};
      }}

      /* Buttons — primary action gets the brand red. */
      button[kind="primary"],
      div[data-testid="stButton"] > button:hover {{
        border-color: {b['primary']};
      }}
    </style>
    """


def inject_custom_css() -> None:
    """Apply the design system. Call once, right after ``st.set_page_config``."""
    st.markdown(_css(), unsafe_allow_html=True)


def section_divider(label: str | None = None) -> None:
    """Render a branded section divider with an optional label.

    Cleaner than ``st.divider()`` for visually separating major
    content blocks inside a tab.
    """
    if label:
        st.markdown(
            f"""
            <div style="
                display:flex;align-items:center;gap:12px;
                margin:18px 0 12px 0;color:{BRAND['ink_muted']};
                font-size:0.85rem;font-weight:600;
                text-transform:uppercase;letter-spacing:0.5px;">
                <span>{label}</span>
                <span style="flex:1;height:1px;background:{BRAND['border']};"></span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<hr style='border:none;border-top:1px solid {BRAND['border']};margin:18px 0;'>",
            unsafe_allow_html=True,
        )
