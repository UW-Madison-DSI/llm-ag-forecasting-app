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


_WEATHER_DEFAULT_FIELDS = ("daily_air_temp_f_avg", "daily_rain_in_tot")
# Fields wiscopy sums per day (rain accumulates); everything else averages.
_WEATHER_SUM_FIELDS = {"daily_rain_in_tot", "daily_rainfall_in"}


@app.get("/proxy/weather")
def proxy_weather(
    station: str | None = Query(
        None, description="Wiscopy station id (e.g. 'ALTN'). "
        "Case-insensitive. Use `stations=` to fetch multiple at once."
    ),
    stations: str | None = Query(
        None, description="Comma-separated list of wiscopy station ids "
        "(e.g. 'ALTN,MAPL'). Use this OR `station`."
    ),
    fields: str | None = Query(
        None, description="Comma-separated wiscopy field names. "
        "Defaults to daily_air_temp_f_avg,daily_rain_in_tot for the disease-tab chart."
    ),
    days: int = Query(60, ge=1, le=400),
    end_date: str | None = Query(None, description="ISO end date; defaults to today."),
    start_date: str | None = Query(None, description="ISO start date; overrides `days` when set."),
):
    """Daily values for one or more Wisconet stations × fields.

    Backward-compatible:
      - `?station=maple` (no `fields`) returns the original
        `{station, start, tavg_f, precip_in}` shape (used by the
        existing Disease tab weather chart).
      - Any call passing `stations=` or `fields=` switches to the
        long-form multi-station / multi-field shape (used by the new
        Weather Data tab).
    """
    if not wiscopy_available():
        raise HTTPException(503, "wiscopy not installed — weather unavailable.")

    from datetime import timedelta

    # ---- Date window ----
    try:
        end = date.fromisoformat(end_date) if end_date else date.today()
        if start_date:
            start = date.fromisoformat(start_date)
        else:
            start = end - timedelta(days=days)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD.")
    if start > end:
        raise HTTPException(400, "start_date must be on or before end_date.")

    # ---- Station list ----
    # Wiscopy uses uppercase 4-char codes (e.g. "ALTN"). Normalize the
    # input so the response data dict is keyed in that canonical form,
    # matching `station_id` everywhere else in the app.
    station_list: list[str] = []
    if stations:
        station_list = [s.strip().upper() for s in stations.split(",") if s.strip()]
    elif station:
        station_list = [station.strip().upper()]
    if not station_list:
        raise HTTPException(400, "Provide `station=` or `stations=`.")

    # ---- Field list ----
    field_list: tuple[str, ...]
    if fields:
        field_list = tuple(f.strip() for f in fields.split(",") if f.strip())
    else:
        field_list = _WEATHER_DEFAULT_FIELDS
    if not field_list:
        raise HTTPException(400, "At least one field is required.")

    # Original (legacy) shape: single station, default fields, no explicit
    # `stations`/`fields`/`start_date` override.
    legacy_shape = (
        not stations and not fields and not start_date
        and len(station_list) == 1
        and field_list == _WEATHER_DEFAULT_FIELDS
    )

    try:
        df = fetch_weather_data(
            tuple(station_list),
            start.isoformat(),
            end.isoformat(),
            field_list,
        )
    except Exception as err:  # noqa: BLE001
        raise HTTPException(502, f"wiscopy: {err}")

    import pandas as pd  # local import — fastapi process always has pandas

    all_dates = pd.date_range(start, end, freq="D")
    dates_iso = [d.strftime("%Y-%m-%d") for d in all_dates]

    # Build the long-format response.
    data: dict[str, dict[str, list]] = {sid: {f: [] for f in field_list} for sid in station_list}
    units: dict[str, str] = {}

    if df is not None and not df.empty:
        if df.index.name:
            df = df.reset_index()

        field_col = (
            "standard_name" if "standard_name" in df.columns
            else "fieldname" if "fieldname" in df.columns
            else None
        )
        time_col = (
            "collection_time" if "collection_time" in df.columns
            else next((c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])),
                      df.columns[0])
        )

        if field_col and "value" in df.columns:
            df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
            if df[time_col].dt.tz is not None:
                df[time_col] = df[time_col].dt.tz_localize(None)
            df = df.dropna(subset=[time_col])

            # Capture units before we lose them in the pivot.
            if "final_units" in df.columns:
                u = (
                    df.dropna(subset=[field_col])
                      .groupby(field_col)["final_units"]
                      .first()
                      .to_dict()
                )
                units = {str(k): str(v) for k, v in u.items() if v is not None}

            # wiscopy returns station_id as 4-char codes (e.g. "ALTN").
            if "station_id" in df.columns:
                df["__sid"] = df["station_id"].astype(str).str.upper()
            else:
                df["__sid"] = station_list[0]

            for sid in station_list:
                sub = df[df["__sid"] == sid]
                if sub.empty:
                    continue
                wide = sub.pivot_table(
                    index=time_col, columns=field_col, values="value", aggfunc="mean",
                )
                if wide.index.tz is not None:
                    wide.index = wide.index.tz_localize(None)
                for fld in field_list:
                    if fld not in wide.columns:
                        data[sid][fld] = [None] * len(all_dates)
                        continue
                    agg = "sum" if fld in _WEATHER_SUM_FIELDS else "mean"
                    by_day = getattr(wide[fld].groupby(wide.index.normalize()), agg)()
                    series = by_day.reindex(all_dates)
                    data[sid][fld] = [
                        round(float(v), 3) if pd.notna(v) else None for v in series.tolist()
                    ]

    # Fill any field/station we couldn't compute with all-nones so the
    # frontend can render gaps without special-casing missing keys.
    for sid in station_list:
        for fld in field_list:
            if not data[sid].get(fld):
                data[sid][fld] = [None] * len(all_dates)

    # Legacy single-station shape: keep existing callers working.
    if legacy_shape:
        sid = station_list[0]
        tavg = data[sid].get("daily_air_temp_f_avg") or [None] * len(all_dates)
        precip = [v if v is not None else 0.0 for v in (data[sid].get("daily_rain_in_tot") or [])]
        if not precip:
            precip = [0.0] * len(all_dates)
        return {
            "station": sid,
            "start": start.isoformat(),
            "tavg_f": tavg,
            "precip_in": precip,
        }

    return {
        "stations": station_list,
        "fields": list(field_list),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "dates": dates_iso,
        "data": data,
        "units": units,
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
    # Wiscopy accepts station_id (the 4-char code) directly and returns
    # the same code in its `station_id` column. Use that as the join key
    # everywhere — avoids the long-name/short-code mismatch that used
    # to make this endpoint return nulls for every station.
    wisc_codes = tuple(sorted(stations_df["station_id"].astype(str).str.upper().unique()))

    try:
        weather = fetch_weather_data(
            wisc_codes, plant_date, forecasting_date,
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
    # bio is keyed by wiscopy's station_id (uppercase 4-char code).
    lookup = {str(row["station_id"]).upper(): row for _, row in bio.iterrows()}

    out = []
    for _, row in stations_df.iterrows():
        rec = lookup.get(str(row["station_id"]).upper())
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
