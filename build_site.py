"""Build a daily JSON snapshot for the static site.

Pipeline:
    1. Fetch the latest disease forecast for every Wisconet station.
    2. Pull weather from wiscopy and run the cereal-rye biomass NLS
       model per station (optional — skipped if wiscopy isn't installed).
    3. Fetch model metadata for each disease model.
    4. Bundle everything into ``site/data/latest.json`` for the browser.
    5. Copy the UW logos into ``site/assets/`` so the static site is
       self-contained.

Usage:
    python build_site.py                 # uses sensible defaults
    python build_site.py 2026-07-20      # specific forecasting date
    python build_site.py 2026-07-20 2026-05-15   # ... and planting date
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from features.api import fetch_forecast, fetch_model_info
from features.config import (
    BIOMASS_DEFAULT_PLANT_DAY,
    BIOMASS_DEFAULT_PLANT_MONTH,
    BIOMASS_DEFAULT_PRECIP_MM,
    BIOMASS_PRECIP_FIELD,
    BIOMASS_TEMP_FIELD,
    BIOMASS_THRESHOLDS,
    CLASS_COLORS,
    DISEASE_OPTIONS,
)
from features.crereal_rye_biomass import biomass_per_station, classify_biomass
from features.data import flatten_features, normalize_class

PROJECT_ROOT = Path(__file__).parent
ASSETS_SRC = PROJECT_ROOT / "assets"
SITE = PROJECT_ROOT / "site"
SITE_DATA = SITE / "data"
SITE_ASSETS = SITE / "assets"

# How many consecutive days of disease forecasts to bundle into
# latest.json. The static site's date picker snaps between these
# entries instantly — no live API call, no CORS dependency.
FORECAST_WINDOW_DAYS = 7

LOGOS = [
    "uw-logo-horizontal-color-web-digital.png",
    "uw-logo-vertical-color-web-digital.png",
]


def _parse_args() -> tuple[date, date]:
    args = sys.argv[1:]
    target = date.fromisoformat(args[0]) if len(args) >= 1 else date.today() - timedelta(days=1)
    if len(args) >= 2:
        plant = date.fromisoformat(args[1])
    else:
        plant = date(target.year, BIOMASS_DEFAULT_PLANT_MONTH, BIOMASS_DEFAULT_PLANT_DAY)
        if plant > target:
            plant = plant.replace(year=plant.year - 1)
    return target, plant


def _disease_models() -> list[dict]:
    out = []
    for label, opts in DISEASE_OPTIONS.items():
        if opts.get("type") == "disease":
            out.append({
                "label": label,
                "type": "disease",
                "risk_field": opts["risk_field"],
                "class_field": opts["class_field"],
                "model_name": opts["model_name"],
            })
    return out


def _biomass_model_entry() -> dict | None:
    for label, opts in DISEASE_OPTIONS.items():
        if opts.get("type") == "biomass":
            return {
                "label": label,
                "type": "biomass",
                "value_field": "biomass",
                "class_field": "biomass_class",
                "unit": "lb/ac",
                "model_name": opts.get("model_name", "cereal_rye_biomass"),
                "thresholds": BIOMASS_THRESHOLDS,
            }
    return None


def build_stations_payload(target_date: date) -> tuple[list[dict], pd.DataFrame]:
    """Fetch the disease forecast and pivot it into per-station dicts."""
    payload = fetch_forecast(target_date.isoformat(), 1)
    df = flatten_features(payload)
    if df.empty:
        raise RuntimeError("Forecast API returned no stations.")
    df = df.drop_duplicates(subset=["station_id"]).copy()

    stations = []
    for _, row in df.iterrows():
        station = {
            "id": str(row["station_id"]),
            "name": str(row["station_name"]),
            "lat": float(row["latitude"]),
            "lon": float(row["longitude"]),
            "city": row.get("city") or None,
            "county": row.get("county") or None,
            "region": row.get("region") or None,
        }
        # Copy every risk + risk_class field straight into the station dict.
        for opts in DISEASE_OPTIONS.values():
            if opts.get("type") != "disease":
                continue
            rf, cf = opts["risk_field"], opts["class_field"]
            v = row.get(rf)
            station[rf] = None if pd.isna(v) else float(v) if isinstance(v, (int, float)) else v
            station[cf] = normalize_class(row.get(cf))
        stations.append(station)
    return stations, df


def add_biomass(stations: list[dict], stations_df: pd.DataFrame, target_date: date, plant_date: date) -> None:
    """Best-effort biomass per station via wiscopy. Skipped on any failure."""
    try:
        from features.weather import fetch_weather_data, wiscopy_available
    except ImportError:
        print("[biomass] features.weather not importable — skipping.")
        return
    if not wiscopy_available():
        print("[biomass] wiscopy not installed — skipping.")
        return

    # Wiscopy accepts the forecast API's station_id (4-char code)
    # directly. Key everything by that so the bundle, the biomass model,
    # and the frontend all align on one identifier.
    wisc_codes = tuple(sorted(set(s["id"].upper() for s in stations)))
    print(f"[biomass] fetching wiscopy for {len(wisc_codes)} stations…")
    try:
        weather = fetch_weather_data(
            wisc_codes, plant_date.isoformat(), target_date.isoformat(),
            (BIOMASS_TEMP_FIELD, BIOMASS_PRECIP_FIELD),
        )
    except Exception as err:  # noqa: BLE001
        print(f"[biomass] wiscopy fetch failed ({type(err).__name__}): {err}")
        return

    if weather is None or weather.empty:
        print("[biomass] wiscopy returned no rows — skipping.")
        return

    try:
        bio = biomass_per_station(
            weather, plant_date,
            temp_field=BIOMASS_TEMP_FIELD,
            precip_field=BIOMASS_PRECIP_FIELD,
            fall_precip_mm=BIOMASS_DEFAULT_PRECIP_MM,
        )
    except Exception as err:  # noqa: BLE001
        print(f"[biomass] per-station compute failed: {err}")
        return

    if bio.empty:
        print("[biomass] model produced no rows.")
        return

    low_max = BIOMASS_THRESHOLDS["low_max"]
    high_min = BIOMASS_THRESHOLDS["high_min"]
    lookup = {str(row["station_id"]).upper(): row for _, row in bio.iterrows()}

    for s in stations:
        rec = lookup.get(s["id"].upper())
        if rec is None:
            s["biomass"] = None
            s["biomass_class"] = "Unknown"
            continue
        v = float(rec["biomass_pred"])
        s["biomass"] = v
        s["biomass_class"] = classify_biomass(v, low_max, high_min)
        s["biomass_gdd_total"] = float(rec["gdd_total"])
        s["biomass_precip_total_mm"] = float(rec.get("precip_total_mm", 0.0))
    print(f"[biomass] attached predictions for {sum(1 for s in stations if s.get('biomass') is not None)} stations.")


def build_weather_bundle(stations: list[dict], plant_date: date, target_date: date) -> dict:
    """Per-station daily Tavg (°F) + precip (in) arrays from planting → target.

    Compact parallel-array shape so a ~250-day × 70-station window stays
    around a few hundred KB. The browser reconstructs dates from
    ``start`` + index, and can replay the biomass model for any
    (planting, forecast) pair entirely client-side.
    """
    try:
        from features.weather import fetch_weather_data, wiscopy_available
    except ImportError:
        print("[weather] features.weather not importable — skipping bundle.")
        return {}
    if not wiscopy_available():
        print("[weather] wiscopy not installed — skipping bundle.")
        return {}

    wisc_codes = tuple(sorted(set(s["id"].upper() for s in stations)))
    print(f"[weather] pulling weather window for {len(wisc_codes)} stations…")
    try:
        df = fetch_weather_data(
            wisc_codes, plant_date.isoformat(), target_date.isoformat(),
            ("daily_air_temp_f_avg", "daily_rain_in_tot"),
        )
    except Exception as err:  # noqa: BLE001
        print(f"[weather] fetch failed: {err}")
        return {}
    if df is None or df.empty:
        print("[weather] wiscopy returned no rows.")
        return {}

    if df.index.name:
        df = df.reset_index()
    field_col = "standard_name" if "standard_name" in df.columns else "fieldname"
    time_col = "collection_time" if "collection_time" in df.columns else next(
        (c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])), df.columns[0]
    )
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    if hasattr(df[time_col].dt, "tz") and df[time_col].dt.tz is not None:
        df[time_col] = df[time_col].dt.tz_localize(None)
    df = df.dropna(subset=[time_col])

    plant_ts = pd.Timestamp(plant_date)
    target_ts = pd.Timestamp(target_date)
    all_dates = pd.date_range(plant_ts, target_ts, freq="D")

    bundle = {}
    for sid, group in df.groupby("station_id"):
        wide = group.pivot_table(index=time_col, columns=field_col, values="value", aggfunc="mean")
        if wide.index.tz is not None:
            wide.index = wide.index.tz_localize(None)

        daily = pd.DataFrame(index=all_dates)
        if "daily_air_temp_f_avg" in wide.columns:
            avg = wide["daily_air_temp_f_avg"].groupby(wide.index.normalize()).mean()
            daily["tavg_f"] = avg.reindex(all_dates)
        if "daily_rain_in_tot" in wide.columns:
            tot = wide["daily_rain_in_tot"].groupby(wide.index.normalize()).sum()
            daily["precip_in"] = tot.reindex(all_dates).fillna(0.0)

        # Bundle keys are uppercase station_id (matches forecast API +
        # what /proxy/weather returns), so the frontend can look them up
        # by `station.id` directly.
        bundle[str(sid).upper()] = {
            "start": plant_ts.strftime("%Y-%m-%d"),
            "tavg_f": [
                round(float(v), 2) if pd.notna(v) else None
                for v in daily.get("tavg_f", pd.Series([None] * len(all_dates))).tolist()
            ],
            "precip_in": [
                round(float(v), 3) if pd.notna(v) else 0.0
                for v in daily.get("precip_in", pd.Series([0.0] * len(all_dates))).tolist()
            ],
        }
    print(f"[weather] bundled {len(bundle)} stations × {len(all_dates)} days.")
    return bundle


def fetch_all_model_info(models: list[dict]) -> dict[str, dict]:
    """Fetch /models/{name} metadata for every distinct slug."""
    seen, out = set(), {}
    for m in models:
        slug = m.get("model_name")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        info = fetch_model_info(slug)
        if info:
            out[slug] = info
        else:
            print(f"[model_info] no metadata for slug='{slug}'")
    return out


def copy_assets() -> None:
    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    for name in LOGOS:
        src = ASSETS_SRC / name
        if src.exists():
            shutil.copy2(src, SITE_ASSETS / name)


def main() -> None:
    target_date, plant_date = _parse_args()
    print(f"Building snapshot — forecast={target_date}, planting={plant_date}")

    # Build disease forecasts for the rolling window (most recent first).
    forecasts_by_date: dict[str, list[dict]] = {}
    available_dates: list[str] = []
    for offset in range(FORECAST_WINDOW_DAYS):
        d = target_date - timedelta(days=offset)
        try:
            day_stations, _ = build_stations_payload(d)
        except Exception as err:  # noqa: BLE001 — skip failed dates, keep going
            print(f"[forecast] skipped {d.isoformat()}: {err}")
            continue
        forecasts_by_date[d.isoformat()] = day_stations
        available_dates.append(d.isoformat())

    if not available_dates:
        raise RuntimeError("No forecasts could be built for any date in the window.")
    available_dates.sort(reverse=True)
    latest_iso = available_dates[0]
    print(f"[forecast] bundled {len(available_dates)} day(s) of forecasts: "
          f"{available_dates[-1]} → {latest_iso}")

    # Biomass + weather bundle only need to be built once; biomass is
    # then re-computed client-side from the weather series for any
    # (planting, forecast) pair the user picks.
    stations = forecasts_by_date[latest_iso]
    add_biomass(stations, None, date.fromisoformat(latest_iso), plant_date)
    weather = build_weather_bundle(stations, plant_date, target_date)

    models = _disease_models()
    biomass_entry = _biomass_model_entry()
    if biomass_entry:
        models.append(biomass_entry)

    model_info = fetch_all_model_info(models)

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecasting_date": latest_iso,
        "plant_date": plant_date.isoformat(),
        "fall_precip_default_mm": BIOMASS_DEFAULT_PRECIP_MM,
        "biomass_thresholds": BIOMASS_THRESHOLDS,
        "models": models,
        # Default/latest day kept under "stations" for the initial page
        # render; the date picker reads from "forecasts" on change.
        "stations": stations,
        "forecasts": forecasts_by_date,
        "available_dates": available_dates,
        "model_info": model_info,
        "class_colors": CLASS_COLORS,
        "weather": weather,
        "weather_window": {
            "start": plant_date.isoformat(),
            "end": target_date.isoformat(),
        },
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    out_path = SITE_DATA / "latest.json"
    out_path.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"Wrote {out_path}  ·  {len(stations)} stations  ·  {len(models)} models")

    copy_assets()
    print(f"Copied {len(LOGOS)} logos → {SITE_ASSETS}")


if __name__ == "__main__":
    main()
