"""Cereal Rye Biomass tab — single-station inference with full debug detail."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# Precomputed daily snapshot produced by web/build_site.py (runs once per
# day via the Docker rebuild cron). The Streamlit app reads from this
# snapshot first so the all-stations biomass map renders instantly.
_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "web" / "site" / "data" / "latest.json"

from features.api import fetch_forecast
from features.config import (
    BIOMASS_DEFAULT_PLANT_DAY,
    BIOMASS_DEFAULT_PLANT_MONTH,
    BIOMASS_DEFAULT_PRECIP_MM,
    BIOMASS_PRECIP_FIELD,
    BIOMASS_TEMP_FIELD,
    BIOMASS_THRESHOLDS,
    CLASS_COLORS,
)
from features.crereal_rye_biomass import (
    biomass_per_station,
    biomass_timeseries,
    classify_biomass,
)
from features.data import flatten_features
from features.map_view import build_map
from features.weather import fetch_weather_data, wiscopy_available

from streamlit_app.tabs.forecast import show_metrics
from streamlit_app.ui import NEUTRAL_TILE_COLOR, color_tile


def _default_plant_date(forecast_date: date) -> date:
    """Default planting date = this year's Sept 15, or last year's if that's after the forecast."""
    plant = date(forecast_date.year, BIOMASS_DEFAULT_PLANT_MONTH, BIOMASS_DEFAULT_PLANT_DAY)
    if plant > forecast_date:
        plant = plant.replace(year=plant.year - 1)
    return plant


def render_biomass_forecast_tab(selected_date: date, model_name: str) -> None:
    """Forecast tab when 'Cereal Rye Biomass' is selected — single-station inference."""
    if not wiscopy_available():
        st.warning(
            "The `wiscopy` package is not installed. "
            "Install it (`pip install wiscopy`) and restart to enable this model."
        )
        return

    # 1. Station roster from the (already disk-cached) forecasting API.
    try:
        payload = fetch_forecast(selected_date.isoformat(), 1)
    except requests.RequestException as err:
        st.error(f"Could not load station roster: {err}")
        return

    stations_df = flatten_features(payload)
    if stations_df.empty:
        st.warning("Station roster is empty for this date.")
        return
    stations_df = stations_df.drop_duplicates(subset=["station_id"]).copy()

    st.session_state["station_options"] = dict(
        zip(stations_df["station_id"].astype(str), stations_df["station_name"].astype(str))
    )

    # 2. Inputs.
    label_to_row = {
        f"{r['station_name']} ({r['station_id']})": r
        for _, r in stations_df.iterrows()
    }
    default_idx = next(
        (i for i, label in enumerate(label_to_row) if "arlington" in label.lower()), 0
    )

    col_s, col_p, col_pr, col_use = st.columns([2, 2, 2, 2])
    with col_s:
        station_label = st.selectbox(
            "Station", options=list(label_to_row.keys()), index=default_idx,
            key="biomass_single_station",
        )
    with col_p:
        plant_date = st.date_input(
            "Planting date", value=_default_plant_date(selected_date),
            key="biomass_single_plant",
            help="Day cereal rye was (or will be) seeded. Used as DOY in the model. "
            "See the [Cereal Rye Biomass thresholds]"
            "(https://cropsandsoils.extension.wisc.edu/articles/"
            "biomass-thresholds-for-cereal-rye-cover-crop-goals/) "
            "(UW–Madison Division of Extension).",
        )
    with col_pr:
        fall_precip_mm = st.number_input(
            "Fall precip fallback (mm)", min_value=0.0, max_value=2000.0,
            value=float(BIOMASS_DEFAULT_PRECIP_MM), step=10.0,
            key="biomass_single_precip",
            help="Used only when wiscopy doesn't return a precip series.",
        )
    with col_use:
        use_real_precip = st.checkbox(
            "Use actual precip from wiscopy", value=True,
            key="biomass_single_use_precip",
            help=f"Pull '{BIOMASS_PRECIP_FIELD}' instead of using the fallback constant.",
        )

    if plant_date >= selected_date:
        st.warning("Planting date must be before the forecasting date.")
        return

    # 3. Wiscopy probe — small, useful when things break.
    with st.expander("🔧 Wiscopy probe (debug)", expanded=False):
        if st.button("Run probe with current inputs", key="biomass_probe_btn"):
            try:
                probe = fetch_weather_data(
                    ("MAPL", "ALTN"),
                    plant_date.isoformat(), selected_date.isoformat(),
                    (BIOMASS_TEMP_FIELD, BIOMASS_PRECIP_FIELD),
                )
            except Exception as err:  # noqa: BLE001
                st.error(f"**{type(err).__name__}**: {str(err).strip() or repr(err)}")
            else:
                if probe is None or probe.empty:
                    st.warning("Probe returned an empty DataFrame.")
                else:
                    st.success(f"Probe OK — {len(probe):,} rows. Columns: {list(probe.columns)}")
                    st.dataframe(probe.head(20), use_container_width=True)

    # 4. All-station biomass map. One bulk wiscopy fetch (cached 1 h) drives
    #    a per-station prediction, classified into Low/Moderate/High using
    #    the same thresholds as the single-station headline below. Stations
    #    where wiscopy returned no data show up as "Inactive" (gray) so the
    #    map roster always matches the disease-forecast tabs.
    _render_all_stations_map(stations_df, plant_date, selected_date, use_real_precip, fall_precip_mm)

    st.markdown("### Single-station detail")

    # 5. Fetch weather for the chosen station.
    chosen = label_to_row[station_label]
    wisc_id = str(chosen["station_id"]).upper()
    fields = (BIOMASS_TEMP_FIELD, BIOMASS_PRECIP_FIELD) if use_real_precip else (BIOMASS_TEMP_FIELD,)

    with st.spinner(f"Pulling weather for {wisc_id}…"):
        try:
            weather = fetch_weather_data(
                (wisc_id,), plant_date.isoformat(), selected_date.isoformat(), fields,
            )
        except Exception as err:  # noqa: BLE001
            st.error(
                f"Could not fetch weather — **{type(err).__name__}**: "
                f"{str(err).strip() or repr(err)}"
            )
            return

    if weather is None or weather.empty:
        st.warning("No weather observations returned for this station/date range.")
        return

    # 6. Run the full pipeline and surface every intermediate value.
    try:
        ts = biomass_timeseries(
            weather, plant_date,
            temp_field=BIOMASS_TEMP_FIELD,
            precip_field=BIOMASS_PRECIP_FIELD if use_real_precip else None,
            fall_precip_mm=None if use_real_precip else fall_precip_mm,
        )
    except Exception as err:  # noqa: BLE001
        st.error(
            f"Could not compute biomass — **{type(err).__name__}**: "
            f"{str(err).strip() or repr(err)}"
        )
        with st.expander("Raw wiscopy DataFrame (for debugging)", expanded=True):
            st.write("Columns:", list(weather.columns))
            st.dataframe(weather.head(50), use_container_width=True)
        return

    if ts.empty:
        st.warning(
            "biomass_timeseries returned no rows. Check that the planting date "
            "falls within the weather window."
        )
        with st.expander("Raw wiscopy DataFrame (for debugging)", expanded=True):
            st.write("Columns:", list(weather.columns))
            st.dataframe(weather.head(50), use_container_width=True)
        return

    final = ts.iloc[-1]
    plant_doy = plant_date.timetuple().tm_yday
    last_obs = ts.index[-1]
    days_since = (pd.Timestamp(last_obs) - pd.Timestamp(plant_date)).days
    biomass_value = float(final["biomass_pred"])
    gdd_total = float(final["gdd_total"])
    precip_total = float(final.get("precip_total_mm", 0.0))
    risk_class = classify_biomass(
        biomass_value, BIOMASS_THRESHOLDS["low_max"], BIOMASS_THRESHOLDS["high_min"]
    )
    bucket_color = CLASS_COLORS.get(risk_class, NEUTRAL_TILE_COLOR)

    # 7. Headline result + inputs panel.
    st.markdown(
        f"""
        <div style="padding: 18px 22px; border-radius: 12px;
                    background: linear-gradient(180deg, rgba(0,0,0,0.02), rgba(0,0,0,0.04));
                    border-left: 6px solid {bucket_color};">
            <div style="color:#6b7280; font-size:0.85rem; font-weight:600;
                        text-transform:uppercase; letter-spacing:0.5px;">
                Predicted biomass — {chosen['station_name']} ({chosen['station_id']})
            </div>
            <div style="font-size:2.6rem; font-weight:800; color:#111827; line-height:1.1;">
                {biomass_value:,.0f} <span style="font-size:1rem; color:#6b7280; font-weight:600;">lb/ac</span>
            </div>
            <div style="margin-top:6px; color:{bucket_color}; font-weight:700;
                        text-transform:uppercase; letter-spacing:0.4px;">
                {risk_class} risk bucket
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        "Biomass risk buckets follow the "
        "[Cereal Rye Biomass thresholds]"
        "(https://cropsandsoils.extension.wisc.edu/articles/"
        "biomass-thresholds-for-cereal-rye-cover-crop-goals/) "
        "(UW–Madison Division of Extension)."
    )

    st.write("")  # spacing

    m1, m2, m3, m4 = st.columns(4)
    color_tile(m1, NEUTRAL_TILE_COLOR, "Plant DOY", plant_doy)
    color_tile(m2, NEUTRAL_TILE_COLOR, "Days since planting", days_since)
    color_tile(m3, NEUTRAL_TILE_COLOR, "Cumulative GDD (°C)", f"{gdd_total:,.0f}")
    color_tile(
        m4, NEUTRAL_TILE_COLOR,
        "Fall precip (mm)" if use_real_precip else "Fall precip — fallback",
        f"{precip_total:,.1f}",
    )

    # 8. Daily detail (handy for spotting bad GDD / precip days).
    with st.expander("Daily breakdown", expanded=False):
        st.dataframe(ts, use_container_width=True)

    with st.expander("Raw wiscopy weather (long-format)", expanded=False):
        st.write("Columns:", list(weather.columns))
        st.dataframe(weather.head(50), use_container_width=True)

    st.caption(f"Last observed: {pd.Timestamp(last_obs).date()}  ·  "
               f"loaded {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def _read_daily_snapshot() -> dict | None:
    """Load web/site/data/latest.json, or None if it doesn't exist / is bad."""
    if not _SNAPSHOT_PATH.is_file():
        return None
    try:
        return json.loads(_SNAPSHOT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _per_station_from_snapshot(
    snapshot: dict, forecast_date_iso: str, plant_date_iso: str
) -> pd.DataFrame | None:
    """Return the precomputed per-station biomass frame if the snapshot matches.

    Match conditions: same forecasting_date AND same plant_date as the
    snapshot was built with. Returns None when the snapshot was built for
    a different (date, planting) combo — caller falls back to live compute.
    """
    if snapshot.get("forecasting_date") != forecast_date_iso:
        return None
    if snapshot.get("plant_date") != plant_date_iso:
        return None
    rows = []
    for s in snapshot.get("stations", []):
        bio = s.get("biomass")
        if bio is None:
            continue
        rows.append({
            "station_id": str(s["id"]),
            "biomass_pred": float(bio),
            "gdd_total": float(s.get("biomass_gdd_total") or 0.0),
            "precip_total_mm": float(s.get("biomass_precip_total_mm") or 0.0),
        })
    return pd.DataFrame(rows) if rows else None


@st.cache_data(
    ttl=86_400,
    persist="disk",
    show_spinner="Loading biomass for all stations…",
)
def _all_station_biomass_map_df(
    forecast_date_iso: str,
    plant_date_iso: str,
    use_real_precip: bool,
    fall_precip_mm: float,
) -> pd.DataFrame:
    """Build the state-wide biomass map_df. Cached on disk for 24 h.

    Resolution order:
        1. **Precomputed daily snapshot** at ``web/site/data/latest.json``.
           Built once a day by ``web/build_site.py`` (runs at Docker build
           time). Used when the snapshot's ``forecasting_date`` and
           ``plant_date`` match the request AND ``use_real_precip`` is True
           (the snapshot is always built with real precip).
        2. **Live wiscopy compute** otherwise — same path as before.

    Returns a DataFrame ready for ``build_map``: station metadata plus
    ``risk_class`` / ``risk_value`` / ``risk_display``.

    Raises:
        Any exception from the forecast API or wiscopy — we deliberately
        do NOT catch them here so a transient failure isn't cached. The
        renderer catches and falls back to an all-Inactive map.
    """
    payload = fetch_forecast(forecast_date_iso, 1)
    stations_df = flatten_features(payload).drop_duplicates(subset=["station_id"]).copy()

    # 1. Try the precomputed snapshot first.
    if use_real_precip:
        snapshot = _read_daily_snapshot()
        if snapshot is not None:
            per_station = _per_station_from_snapshot(snapshot, forecast_date_iso, plant_date_iso)
            if per_station is not None and not per_station.empty:
                return _merge_biomass_into_map_df(stations_df, per_station)

    # 2. Fall back to live wiscopy compute.
    all_ids = tuple(sorted({str(s).upper() for s in stations_df["station_id"]}))
    fields = (BIOMASS_TEMP_FIELD, BIOMASS_PRECIP_FIELD) if use_real_precip else (BIOMASS_TEMP_FIELD,)

    bulk_weather = fetch_weather_data(all_ids, plant_date_iso, forecast_date_iso, fields)

    plant_date = pd.Timestamp(plant_date_iso).date()
    per_station = pd.DataFrame()
    if bulk_weather is not None and not bulk_weather.empty:
        per_station = biomass_per_station(
            bulk_weather, plant_date,
            temp_field=BIOMASS_TEMP_FIELD,
            precip_field=BIOMASS_PRECIP_FIELD if use_real_precip else None,
            fall_precip_mm=None if use_real_precip else fall_precip_mm,
        )

    return _merge_biomass_into_map_df(stations_df, per_station)


def _merge_biomass_into_map_df(
    stations_df: pd.DataFrame, per_station: pd.DataFrame
) -> pd.DataFrame:
    """Attach biomass predictions + risk_class onto the station roster."""
    map_df = stations_df.copy()
    map_df["station_id_upper"] = map_df["station_id"].astype(str).str.upper()

    if per_station is not None and not per_station.empty:
        ps = per_station.copy()
        ps["station_id_upper"] = ps["station_id"].astype(str).str.upper()
        ps = ps.drop(columns=["station_id"])
        map_df = map_df.merge(ps, on="station_id_upper", how="left")
    else:
        map_df["biomass_pred"] = float("nan")

    low_max = BIOMASS_THRESHOLDS["low_max"]
    high_min = BIOMASS_THRESHOLDS["high_min"]
    map_df["risk_class"] = [
        classify_biomass(v, low_max, high_min) if pd.notna(v) else "Inactive"
        for v in map_df["biomass_pred"]
    ]
    map_df["risk_value"] = map_df["biomass_pred"]
    map_df["risk_display"] = [
        f"{v:,.0f} lb/ac" if pd.notna(v) else "—"
        for v in map_df["biomass_pred"]
    ]
    return map_df.drop(columns=["station_id_upper"])


def _render_all_stations_map(
    stations_df: pd.DataFrame,
    plant_date: date,
    forecast_date: date,
    use_real_precip: bool,
    fall_precip_mm: float,
) -> None:
    """Render the state-wide biomass map. Result is disk-cached for 24 h.

    On a wiscopy / forecast-API error we don't cache; we fall back to an
    all-Inactive map so the layout stays intact and the user sees a
    warning explaining the situation.
    """
    if plant_date >= forecast_date:
        return

    try:
        map_df = _all_station_biomass_map_df(
            forecast_date.isoformat(),
            plant_date.isoformat(),
            bool(use_real_precip),
            float(fall_precip_mm),
        )
    except Exception as err:  # noqa: BLE001
        st.warning(
            f"Could not compute state-wide biomass — "
            f"**{type(err).__name__}**: {str(err).strip() or repr(err)}"
        )
        map_df = _merge_biomass_into_map_df(stations_df, pd.DataFrame())

    show_metrics(map_df)
    st.plotly_chart(build_map(map_df, "Cereal Rye Biomass"), use_container_width=True)
