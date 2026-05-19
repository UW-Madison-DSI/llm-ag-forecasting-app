"""Cached HTTP client for the UW–Madison Ag Forecasting API."""

import requests
import streamlit as st

from features.config import (
    API_URL,
    CACHE_TTL_SECONDS,
    MODEL_INFO_TTL_SECONDS,
    MODEL_INFO_URL_TEMPLATE,
)


@st.cache_data(
    ttl=CACHE_TTL_SECONDS,
    persist="disk",
    show_spinner="Fetching forecast from Wisconet…",
)
def fetch_forecast(forecasting_date: str, risk_days: int = 1) -> dict:
    """Fetch a daily risk forecast for every Wisconet station.

    Cached on disk for 24 h (see ``CACHE_TTL_SECONDS``) keyed by
    ``(forecasting_date, risk_days)``, so the network call happens at
    most once per day per combination even across app restarts. Call
    ``fetch_forecast.clear()`` to force a refetch (the sidebar's
    "Refresh data" button does this).

    Args:
        forecasting_date: ISO date string, e.g. ``"2026-07-15"``.
        risk_days: Forecast horizon in days (1–7 per the API).

    Returns:
        The parsed JSON FeatureCollection-style payload.

    Raises:
        requests.HTTPError: Non-2xx response from the API.
        requests.RequestException: Network/transport failure.
    """
    response = requests.get(
        API_URL,
        params={"forecasting_date": forecasting_date, "risk_days": risk_days},
        headers={"accept": "application/json"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(
    ttl=MODEL_INFO_TTL_SECONDS,
    persist="disk",
    show_spinner=False,
)
def fetch_model_info(model_name: str) -> dict | None:
    """Fetch static metadata for one forecasting model.

    Returns the model's description, input variables, model type,
    risk-output scale, inactive rule, and version. Cached on disk for
    a week — this content is essentially static.

    Args:
        model_name: The API's short model id (e.g. ``"tarspot"``).

    Returns:
        Parsed JSON dict on success, or ``None`` if the API returns
        4xx/5xx or is unreachable. Callers should show a graceful
        fallback when ``None``.
    """
    url = MODEL_INFO_URL_TEMPLATE.format(model_name=model_name)
    try:
        response = requests.get(
            url,
            headers={"accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return response.json()
