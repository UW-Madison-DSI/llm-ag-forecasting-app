"""Feature-usage tracking via Google Analytics (GA4) custom events.

Lets the team see *which features* get used during user testing — which
disease model is chosen, how the risk-days slider moves, when data is
refreshed, when the PDF export is used. The data lives in GA4 (Google's
servers), never in this repository: the code contains only event *names*,
no user data, and nothing is written to disk.

How it works
------------
``streamlit_app/analytics.py`` injects gtag.js into Streamlit's page, so a
``gtag`` function exists on the top window. Streamlit widget changes happen
server-side in Python, so we bridge to the browser the same way the print
button does — a tiny component that calls ``window.parent.gtag(...)``.

Usage
-----
Call :func:`queue_event` from a widget ``on_change`` / ``on_click`` callback
(so it fires only on a real interaction), then call :func:`flush_events` once
near the end of the page render to emit everything queued this run::

    st.selectbox("Disease model", opts, key="disease",
                 on_change=lambda: queue_event("disease_selected",
                                                model=st.session_state["disease"]))
    ...
    flush_events()

Privacy: log feature names and UI choices only — never a field's coordinates
or anything that identifies a grower.
"""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

_PENDING_KEY = "_ga_pending_events"


def queue_event(event: str, **params) -> None:
    """Queue a GA4 event to be emitted on the next :func:`flush_events`.

    Safe to call from ``on_change`` / ``on_click`` callbacks, which run before
    the rerun; the event is flushed once during that rerun.

    Args:
        event: Short event name, e.g. ``"disease_selected"``.
        **params: JSON-serializable detail, e.g. ``model="tarspot"``.
    """
    st.session_state.setdefault(_PENDING_KEY, []).append(
        {"event": event, "params": params}
    )


def flush_events() -> None:
    """Emit any queued events as ``gtag('event', …)`` calls in the parent frame.

    No-op (renders nothing) when there's nothing queued, so normal reruns add
    no DOM clutter. No-op gracefully when gtag isn't present (GA_MEASUREMENT_ID
    unset).
    """
    events = st.session_state.pop(_PENDING_KEY, [])
    if not events:
        return

    calls = "\n".join(
        f"p.gtag('event', {json.dumps(e['event'])}, {json.dumps(e['params'])});"
        for e in events
    )
    components.html(
        f"""
        <script>
          (function () {{
            var p = window.parent;
            if (p && typeof p.gtag === "function") {{
              {calls}
            }}
          }})();
        </script>
        """,
        height=0,
    )
