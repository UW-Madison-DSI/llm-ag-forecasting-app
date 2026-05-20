"""FastAPI proxy: the dynamic half of the static-site deployment.

Endpoints (all under /proxy/, mounted that way so the nginx
``location /proxy/`` block can pass through transparently):

    GET /proxy/health        — liveness + wiscopy availability
    GET /proxy/forecast      — disease forecast for one date (CORS shim)
    GET /proxy/model_info    — model description / variables / version
    GET /proxy/biomass       — cereal-rye biomass per station

The endpoints just call the same helpers the Streamlit app and
build_site.py use, so there's a single source of truth for the model
math and the upstream API calls.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Streamlit's @st.cache_data still works here — it just logs a warning
# about no Streamlit runtime and falls back to in-memory caching, which
# is exactly what we want for a long-running FastAPI process. Mute the
# warning so the uvicorn console stays readable.
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

from features.api import fetch_forecast, fetch_model_info  # noqa: E402
from features.config import (
    BIOMASS_DEFAULT_PRECIP_MM,
    BIOMASS_PRECIP_FIELD,
    BIOMASS_TEMP_FIELD,
    BIOMASS_THRESHOLDS,
)
from features.crereal_rye_biomass import biomass_per_station, classify_biomass
from features.data import flatten_features
from features.weather import fetch_weather_data, wiscopy_available

log = logging.getLogger("backend")

app = FastAPI(title="Ag Forecasting Proxy", docs_url="/proxy/docs", redoc_url=None)

# Same-origin in production (nginx terminates), but allow any origin
# when uvicorn is hit directly (e.g. `uvicorn backend.main:app` for dev).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/proxy/health")
def health():
    return {"status": "ok", "wiscopy": wiscopy_available()}


@app.get("/proxy/forecast")
def proxy_forecast(
    forecasting_date: str = Query(..., description="ISO date YYYY-MM-DD"),
    risk_days: int = Query(1, ge=1, le=7),
):
    """Disease forecast for one date. Drop-in for the upstream API."""
    try:
        date.fromisoformat(forecasting_date)
    except ValueError:
        raise HTTPException(400, "forecasting_date must be YYYY-MM-DD.")
    try:
        return fetch_forecast(forecasting_date, risk_days)
    except Exception as err:  # noqa: BLE001
        log.exception("Forecast proxy failed")
        raise HTTPException(502, f"Upstream error: {err}")


@app.get("/proxy/model_info")
def proxy_model_info(model_name: str = Query(..., min_length=1)):
    info = fetch_model_info(model_name)
    if not info:
        raise HTTPException(404, f"No metadata for model `{model_name}`.")
    return info


@app.get("/proxy/weather")
def proxy_weather(
    station: str = Query(..., min_length=1, description="Wiscopy station id (lowercase name)."),
    days: int = Query(60, ge=1, le=400),
    end_date: str | None = Query(None, description="ISO end date; defaults to today."),
):
    """Daily Tavg (°F) + precip (in) for one Wisconet station.

    Returns the same shape the bundled `weather` field in latest.json
    uses, so the frontend can drop it straight into the chart.
    """
    if not wiscopy_available():
        raise HTTPException(503, "wiscopy not installed — weather unavailable.")

    from datetime import timedelta
    try:
        end = date.fromisoformat(end_date) if end_date else date.today()
    except ValueError:
        raise HTTPException(400, "end_date must be YYYY-MM-DD.")
    start = end - timedelta(days=days)

    try:
        df = fetch_weather_data(
            (station.lower(),),
            start.isoformat(),
            end.isoformat(),
            ("daily_air_temp_f_avg", "daily_rain_in_tot"),
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"wiscopy: {err}")

    empty = {
        "station": station, "start": start.isoformat(),
        "tavg_f": [], "precip_in": [],
    }
    if df is None or df.empty:
        return empty

    import pandas as pd  # local import — fastapi process always has pandas
    if df.index.name:
        df = df.reset_index()

    field_col = (
        "standard_name" if "standard_name" in df.columns
        else "fieldname" if "fieldname" in df.columns
        else None
    )
    if field_col is None or "value" not in df.columns:
        return empty

    time_col = (
        "collection_time" if "collection_time" in df.columns
        else next((c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])),
                  df.columns[0])
    )
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    if df[time_col].dt.tz is not None:
        df[time_col] = df[time_col].dt.tz_localize(None)
    df = df.dropna(subset=[time_col])

    wide = df.pivot_table(index=time_col, columns=field_col, values="value", aggfunc="mean")
    if wide.index.tz is not None:
        wide.index = wide.index.tz_localize(None)

    all_dates = pd.date_range(start, end, freq="D")
    daily = pd.DataFrame(index=all_dates)

    if "daily_air_temp_f_avg" in wide.columns:
        tavg = wide["daily_air_temp_f_avg"].groupby(wide.index.normalize()).mean()
        daily["tavg_f"] = tavg.reindex(all_dates)
    if "daily_rain_in_tot" in wide.columns:
        prc = wide["daily_rain_in_tot"].groupby(wide.index.normalize()).sum()
        daily["precip_in"] = prc.reindex(all_dates).fillna(0.0)

    return {
        "station": station,
        "start": start.isoformat(),
        "tavg_f": [
            round(float(v), 2) if pd.notna(v) else None
            for v in daily.get("tavg_f", pd.Series([None] * len(all_dates))).tolist()
        ],
        "precip_in": [
            round(float(v), 3) if pd.notna(v) else 0.0
            for v in daily.get("precip_in", pd.Series([0.0] * len(all_dates))).tolist()
        ],
    }


@app.get("/proxy/biomass")
def proxy_biomass(
    forecasting_date: str = Query(...),
    plant_date: str = Query(...),
    fall_precip_mm: float = Query(default=BIOMASS_DEFAULT_PRECIP_MM, ge=0, le=2000),
):
    """Run the cereal rye biomass NLS model for every station.

    Server-side because the model needs wiscopy (Python only), and
    because computing biomass for ~70 stations × ~250 days in the
    browser is wasteful.
    """
    if not wiscopy_available():
        raise HTTPException(503, "wiscopy not installed — biomass unavailable.")

    try:
        plant_d = date.fromisoformat(plant_date)
        fcst_d = date.fromisoformat(forecasting_date)
    except ValueError as err:
        raise HTTPException(400, str(err))
    if plant_d >= fcst_d:
        raise HTTPException(400, "plant_date must precede forecasting_date.")

    try:
        payload = fetch_forecast(forecasting_date, 1)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"Forecast API: {err}")

    stations_df = flatten_features(payload)
    if stations_df.empty:
        raise HTTPException(404, "No stations returned.")
    stations_df = stations_df.drop_duplicates(subset=["station_id"]).copy()
    wisc_names = tuple(sorted(stations_df["station_name"].astype(str).str.lower().unique()))

    try:
        weather = fetch_weather_data(
            wisc_names, plant_date, forecasting_date,
            (BIOMASS_TEMP_FIELD, BIOMASS_PRECIP_FIELD),
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"wiscopy: {err}")

    try:
        bio = biomass_per_station(
            weather, plant_d,
            temp_field=BIOMASS_TEMP_FIELD,
            precip_field=BIOMASS_PRECIP_FIELD,
            fall_precip_mm=fall_precip_mm,
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(500, f"Biomass model: {err}")

    low_max = BIOMASS_THRESHOLDS["low_max"]
    high_min = BIOMASS_THRESHOLDS["high_min"]
    lookup = {row["station_id"]: row for _, row in bio.iterrows()}

    out = []
    for _, row in stations_df.iterrows():
        rec = lookup.get(str(row["station_name"]).lower())
        v = float(rec["biomass_pred"]) if rec is not None else None
        out.append({
            "station_id": str(row["station_id"]),
            "station_name": str(row["station_name"]),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "biomass_pred": v,
            "biomass_class": classify_biomass(v, low_max, high_min),
            "gdd_total": float(rec["gdd_total"]) if rec is not None else None,
            "precip_total_mm": float(rec["precip_total_mm"]) if rec is not None else None,
        })
    return {
        "forecasting_date": forecasting_date,
        "plant_date": plant_date,
        "fall_precip_mm": fall_precip_mm,
        "stations": out,
    }


# ---------------------------------------------------------------------------
# Static site mount — served from the same uvicorn process so local dev is a
# single command (`uvicorn backend.main:app --reload --port 8000`).
#
# In production this mount is harmless: nginx (in front of uvicorn) intercepts
# /, /assets/, /lib/, /data/ before they ever reach FastAPI, so it never
# actually serves them there.
#
# `html=True` makes FastAPI fall back to index.html on directory requests,
# which is what an SPA-ish single-page site expects.
# ---------------------------------------------------------------------------

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
if SITE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
