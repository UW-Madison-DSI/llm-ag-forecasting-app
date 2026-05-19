"""Streamlit entrypoint for the WI crop disease risk dashboard.

Composes the sidebar controls, metric tiles, station map, data table,
and weather time-series from the building blocks in the ``features``
package. Run with::

    streamlit run app.py
"""

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

_ASSETS = Path(__file__).parent / "assets"
LOGO_FULL = _ASSETS / "uw-logo-horizontal-color-web-digital.png"  # wide variant, expanded sidebar
LOGO_ICON = _ASSETS / "uw-logo-vertical-color-web-digital.png"    # compact variant, collapsed sidebar

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
    RISK_TRENDS_DAYS,
    WEATHER_DEFAULT_DAYS,
    WEATHER_FIELDS,
)
from features.crereal_rye_biomass import (
    biomass_per_station,
    biomass_timeseries,
    classify_biomass,
)
from features.data import flatten_features, prepare_disease_df
from features.map_view import build_map
from features.weather import fetch_weather_data, wiscopy_available


st.set_page_config(
    page_title="WI Crop Disease Risk Forecast",
    page_icon="🌽",
    layout="wide",
)

# Persistent institutional logo (top of sidebar, plus a tiny icon when
# the sidebar is collapsed). Falls back silently if either file is missing.
if LOGO_FULL.exists():
    st.logo(
        str(LOGO_FULL),
        size="large",
        icon_image=str(LOGO_ICON) if LOGO_ICON.exists() else None,
    )
    # st.logo caps at size="large"; bump it further via scoped CSS.
    # Change the px value below to resize again.
    st.markdown(
        """
        <style>
            [data-testid="stSidebarHeader"] { padding-top: 1rem; padding-bottom: 1rem; }
            [data-testid="stSidebarHeader"] img,
            [data-testid="stLogo"] {
                height: 90px !important;
                max-height: 90px !important;
                width: auto !important;
                max-width: 100% !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

st.title("🌽 Wisconsin Crop Disease Risk Forecast")
st.caption(
    "Daily risk forecast from the UW–Madison Ag Forecasting API (Wisconet stations). "
    "Data is cached on disk for 24 h per (date, risk_days)."
)


def sidebar_controls() -> tuple[date, int, str]:
    """Render the sidebar and return the user's current selections.

    Returns:
        ``(selected_date, risk_days, disease_label)`` chosen by the user.
        ``disease_label`` is a key into :data:`DISEASE_OPTIONS`.
    """
    with st.sidebar:
        st.header("Controls")
        selected_date = st.date_input(
            "Forecasting date",
            value=date.today() - timedelta(days=1),
            max_value=date.today(),
        )
        risk_days = st.slider("Risk days", min_value=1, max_value=7, value=1)
        disease_label = st.selectbox("Disease model", list(DISEASE_OPTIONS.keys()))
        if st.button("🔄 Refresh data"):
            fetch_forecast.clear()
            st.rerun()
    return selected_date, risk_days, disease_label


def _color_tile(col, color: str, label: str, value, tooltip: str = "") -> None:
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


def show_metrics(df: pd.DataFrame) -> None:
    """Render the five summary metric tiles above the map.

    Uses case-insensitive substring matching on ``risk_class`` so values
    like ``"High Risk"`` or ``"high"`` still count as High — the API
    isn't perfectly consistent across models/seasons.
    """
    if "risk_class" not in df.columns:
        st.warning("`risk_class` column is missing — counts unavailable.")
        return

    rc = df["risk_class"].astype(str).fillna("")

    def count_contains(pattern: str) -> int:
        return int(rc.str.contains(pattern, case=False, regex=True, na=False).sum())

    total = len(df)
    inactive_or_unknown = rc.str.contains(r"inactive|unknown|none|n/?a", case=False, regex=True, na=False)
    active = int((~inactive_or_unknown).sum())
    high = count_contains(r"\bhigh\b")
    moderate = count_contains(r"\bmoderate\b|\bmedium\b")
    low = count_contains(r"\blow\b")

    neutral = "#34495e"
    cols = st.columns(5)
    _color_tile(cols[0], neutral, "Total stations", total)
    _color_tile(
        cols[1], neutral, "Active stations", active,
        tooltip="Stations where the selected model is currently running (not Inactive).",
    )
    _color_tile(cols[2], CLASS_COLORS["High"], "High risk", high)
    _color_tile(cols[3], CLASS_COLORS["Moderate"], "Moderate risk", moderate)
    _color_tile(cols[4], CLASS_COLORS["Low"], "Low risk", low)

    # Debug aid: surface the actual values the API returned so it's
    # easy to see if a model is using unexpected class labels.
    with st.expander("🔍 Risk-class values seen", expanded=False):
        counts = rc.value_counts(dropna=False).rename_axis("risk_class").reset_index(name="stations")
        st.dataframe(counts, use_container_width=True, hide_index=True)


def show_table(df: pd.DataFrame, risk_field: str, class_field: str) -> None:
    """Render the collapsible per-station data table."""
    with st.expander("Station data table"):
        cols = [
            "station_id", "station_name", "city", "county", "region",
            "latitude", "longitude", risk_field, class_field, "forecasting_date",
        ]
        cols = [c for c in cols if c in df.columns]
        st.dataframe(df[cols].sort_values(class_field), use_container_width=True)


def show_model_info(model_name: str, disease_label: str) -> None:
    """Render an "About this model" expander with metadata from the API.

    Pulls description, input variables, model type, risk-output scale,
    inactive rule, and version from the ``/models/{model_name}`` endpoint.
    Falls back silently if the lookup fails (e.g. unknown model name).
    """
    info = fetch_model_info(model_name)
    with st.expander(f"📖 About this model — {disease_label}", expanded=False):
        if not info:
            st.info(
                f"No metadata available for model `{model_name}`. "
                "Check the model name in `features/config.py`."
            )
            return

        name = info.get("name", model_name)
        crop = info.get("crop")
        version = info.get("version")
        header = f"**{name}**"
        if crop:
            header += f"  ·  crop: *{crop}*"
        if version:
            header += f"  ·  v{version}"
        st.markdown(header)

        description = info.get("description")
        if description:
            st.markdown(description)

        col1, col2 = st.columns(2)
        with col1:
            if info.get("model_type"):
                st.markdown(f"**Model type:** {info['model_type']}")
            if info.get("risk_output"):
                st.markdown(f"**Risk output:** {info['risk_output']}")
        with col2:
            if info.get("inactive_rule"):
                st.markdown(f"**Inactive rule:** {info['inactive_rule']}")

        variables = info.get("variables") or []
        if variables:
            st.markdown("**Input variables**")
            st.markdown("\n".join(f"- `{v}`" for v in variables))


def render_forecast_tab(selected_date: date, risk_days: int, disease_label: str) -> None:
    """Render the Disease Forecast tab: metrics, map, model info, table.

    Also stashes the (station_id → station_name) mapping in
    ``st.session_state`` so the Weather and Risk Trends tabs can
    populate their pickers without re-fetching.
    """
    opts = DISEASE_OPTIONS[disease_label]
    risk_field = opts["risk_field"]
    class_field = opts["class_field"]
    model_name = opts["model_name"]

    try:
        payload = fetch_forecast(selected_date.isoformat(), risk_days)
    except requests.HTTPError as err:
        st.error(f"API returned an error: {err.response.status_code} — {err.response.text[:200]}")
        return
    except requests.RequestException as err:
        st.error(f"Could not reach the forecasting API: {err}")
        return

    df = flatten_features(payload)
    if df.empty:
        st.warning("No station data returned for this date.")
        return

    # Share station roster with the Weather and Risk Trends tabs.
    st.session_state["station_options"] = dict(
        zip(df["station_id"].astype(str), df["station_name"].astype(str))
    )

    map_df = prepare_disease_df(df, risk_field, class_field)
    show_metrics(map_df)
    st.plotly_chart(build_map(map_df, disease_label), use_container_width=True)
    show_model_info(model_name, disease_label)
    show_table(map_df, risk_field, class_field)

    st.caption(f"Last loaded: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def render_weather_tab() -> None:
    """Render the Weather Data tab: time-series of one field per station.

    Reads the station roster from ``st.session_state`` (populated by
    the Forecast tab). If wiscopy isn't installed, shows install
    instructions instead of crashing.
    """
    if not wiscopy_available():
        st.warning(
            "The `wiscopy` package is not installed. "
            "Install it (`pip install wiscopy`) and restart the app to enable this tab."
        )
        return

    station_options: dict[str, str] = st.session_state.get("station_options", {})
    if not station_options:
        st.info("Load the Forecast tab first so the station roster is available.")
        return

    # Map display label → wiscopy station id (lowercased station name,
    # matching the wiscopy convention from your example).
    label_to_wid = {
        f"{name} ({sid})": name.lower() for sid, name in station_options.items()
    }
    default_labels = list(label_to_wid.keys())[:2]

    col_l, col_r = st.columns([3, 2])
    with col_l:
        selected_labels = st.multiselect(
            "Stations",
            options=list(label_to_wid.keys()),
            default=default_labels,
        )
    with col_r:
        default_end = date.today()
        default_start = default_end - timedelta(days=WEATHER_DEFAULT_DAYS)
        date_range = st.date_input(
            "Date range",
            value=(default_start, default_end),
            max_value=date.today(),
        )

    field = st.selectbox("Weather field", options=WEATHER_FIELDS, index=0)

    if not selected_labels:
        st.info("Pick at least one station above.")
        return
    if not isinstance(date_range, tuple) or len(date_range) != 2:
        st.info("Pick a start and end date.")
        return

    start, end = date_range
    wisco_ids = tuple(label_to_wid[label] for label in selected_labels)

    try:
        df = fetch_weather_data(wisco_ids, start.isoformat(), end.isoformat(), (field,))
    except Exception as err:  # wiscopy raises various; treat all as recoverable
        st.error(f"Could not fetch weather data: {err}")
        return

    if df is None or df.empty:
        st.warning("No observations returned for these inputs.")
        return

    units = df["final_units"].iloc[0] if "final_units" in df.columns else ""
    title = f"{field} ({units})" if units else field

    # Plotly needs the time on a column, not the index.
    plot_df = df.reset_index()
    time_col = plot_df.columns[0]
    fig = px.line(
        plot_df,
        x=time_col,
        y="value",
        color="station_id" if "station_id" in plot_df.columns else None,
        title=title,
        labels={"value": units or "value", time_col: "time"},
    )
    fig.update_layout(height=520, margin={"r": 0, "t": 50, "l": 0, "b": 0})
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Raw data"):
        st.dataframe(df, use_container_width=True)


def render_risk_trends_tab(selected_date: date, disease_label: str) -> None:
    """Render the Risk Trends tab: N-day risk time-series per station.

    Always fetches with ``risk_days=RISK_TRENDS_DAYS`` (independent of
    the sidebar slider, which controls the Forecast tab) so this view
    is consistently a multi-day trend chart.

    The API returns one timeseries entry per forecast day. We unstack
    those into long format and plot risk-over-time, one line per
    selected station. Sentinel ``-1`` values (model inactive) are
    converted to gaps so they don't drag the line below zero.
    """
    opts = DISEASE_OPTIONS[disease_label]
    risk_field = opts["risk_field"]
    class_field = opts["class_field"]

    station_options: dict[str, str] = st.session_state.get("station_options", {})
    if not station_options:
        st.info("Load the Forecast tab first so the station roster is available.")
        return

    label_to_sid = {f"{name} ({sid})": sid for sid, name in station_options.items()}
    default_labels = list(label_to_sid.keys())[:5]
    selected_labels = st.multiselect(
        "Stations",
        options=list(label_to_sid.keys()),
        default=default_labels,
        help="Compare up to a handful of stations to see how risk evolves.",
    )

    if not selected_labels:
        st.info("Pick at least one station above.")
        return

    selected_sids = {label_to_sid[label] for label in selected_labels}

    try:
        payload = fetch_forecast(selected_date.isoformat(), RISK_TRENDS_DAYS)
    except requests.HTTPError as err:
        st.error(f"API returned an error: {err.response.status_code} — {err.response.text[:200]}")
        return
    except requests.RequestException as err:
        st.error(f"Could not reach the forecasting API: {err}")
        return

    df = flatten_features(payload)
    if df.empty:
        st.warning("No data returned for this date.")
        return

    df = df[df["station_id"].astype(str).isin(selected_sids)].copy()
    if df.empty:
        st.warning("No data for the selected stations.")
        return

    # Prefer the inner "forecasting_date" (the day being predicted);
    # fall back to the outer timeseries "date" if missing.
    date_col = "forecasting_date" if "forecasting_date" in df.columns else "date"
    df["plot_date"] = pd.to_datetime(df[date_col], errors="coerce")
    df[risk_field] = pd.to_numeric(df[risk_field], errors="coerce")
    # -1 marks "model inactive" — show as a gap, not a dip.
    df["risk_plot"] = df[risk_field].where(df[risk_field] != -1)

    df = df.sort_values(["station_name", "plot_date"])

    fig = px.line(
        df,
        x="plot_date",
        y="risk_plot",
        color="station_name",
        markers=True,
        title=f"{disease_label} — {RISK_TRENDS_DAYS}-day risk forecast",
        labels={"plot_date": "Forecasting date", "risk_plot": "Risk", "station_name": "Station"},
    )
    fig.update_layout(height=520, margin={"r": 0, "t": 50, "l": 0, "b": 0})
    fig.update_traces(connectgaps=False)
    st.plotly_chart(fig, use_container_width=True)

    # Companion: stacked bar of risk-class counts per day, so you can
    # see how many stations cross into High/Moderate each day.
    if class_field in df.columns:
        class_counts = (
            df.assign(risk_class=df[class_field].astype(str).str.title())
            .groupby(["plot_date", "risk_class"])
            .size()
            .reset_index(name="stations")
        )
        present = [c for c in CLASS_COLORS if c in class_counts["risk_class"].unique()]
        fig_bar = px.bar(
            class_counts,
            x="plot_date",
            y="stations",
            color="risk_class",
            color_discrete_map=CLASS_COLORS,
            category_orders={"risk_class": present},
            title="Risk class distribution across selected stations",
            labels={"plot_date": "Forecasting date", "stations": "Stations"},
        )
        fig_bar.update_layout(height=360, margin={"r": 0, "t": 50, "l": 0, "b": 0})
        st.plotly_chart(fig_bar, use_container_width=True)

    with st.expander("Raw data"):
        cols = ["station_id", "station_name", "plot_date", risk_field, class_field]
        cols = [c for c in cols if c in df.columns]
        st.dataframe(df[cols], use_container_width=True)


def _show_biomass_metrics(df: pd.DataFrame) -> None:
    """Five metric tiles matching the disease layout but for biomass."""
    if df.empty:
        return
    total = len(df)
    has_pred = int(df["biomass_pred"].notna().sum())
    high = int((df["risk_class"] == "High").sum())
    moderate = int((df["risk_class"] == "Moderate").sum())
    low = int((df["risk_class"] == "Low").sum())

    neutral = "#34495e"
    cols = st.columns(5)
    _color_tile(cols[0], neutral, "Total stations", total)
    _color_tile(
        cols[1], neutral, "With prediction", has_pred,
        tooltip="Stations where wiscopy returned weather and the model produced a value.",
    )
    _color_tile(cols[2], CLASS_COLORS["High"], "High biomass", high)
    _color_tile(cols[3], CLASS_COLORS["Moderate"], "Moderate biomass", moderate)
    _color_tile(cols[4], CLASS_COLORS["Low"], "Low biomass", low)


def render_biomass_forecast_tab(selected_date: date, model_name: str) -> None:
    """Forecast tab when 'Cereal Rye Biomass' is selected — single-station inference.

    Lets the user pick one station and inspect the full inference pipeline:
    raw weather sample, daily Tavg, cumulative GDD, cumulative precip, and
    the final biomass prediction at the forecasting date. Useful both as a
    sanity check and as the per-field debug entry point before we scale
    back up to a multi-station map.
    """
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
    stations_df["__wisc_name"] = stations_df["station_name"].astype(str).str.lower()

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
        plant_default = date(
            date.today().year, BIOMASS_DEFAULT_PLANT_MONTH, BIOMASS_DEFAULT_PLANT_DAY
        )
        if plant_default > selected_date:
            plant_default = plant_default.replace(year=plant_default.year - 1)
        plant_date = st.date_input(
            "Planting date", value=plant_default, key="biomass_single_plant",
            help="Day cereal rye was (or will be) seeded. Used as DOY in the model.",
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
                    ("maple", "arlington"),
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

    # 4. Fetch weather for the chosen station.
    chosen = label_to_row[station_label]
    wisc_id = str(chosen["__wisc_name"])
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

    # 5. Run the full pipeline and surface every intermediate value.
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
    risk_class = classify_biomass(biomass_value,
                                  BIOMASS_THRESHOLDS["low_max"],
                                  BIOMASS_THRESHOLDS["high_min"])
    bucket_color = CLASS_COLORS.get(risk_class, "#34495e")

    # 6. Headline result + inputs panel.
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

    st.write("")  # spacing

    m1, m2, m3, m4 = st.columns(4)
    _color_tile(m1, "#34495e", "Plant DOY", plant_doy)
    _color_tile(m2, "#34495e", "Days since planting", days_since)
    _color_tile(m3, "#34495e", "Cumulative GDD (°C)", f"{gdd_total:,.0f}")
    _color_tile(
        m4, "#34495e",
        "Fall precip (mm)" if use_real_precip else "Fall precip — fallback",
        f"{precip_total:,.1f}",
    )

    # 7. Single-station map so it visually anchors the prediction.
    map_row = stations_df[stations_df["station_id"] == chosen["station_id"]].copy()
    map_row["risk_class"] = risk_class
    map_row["risk_value"] = biomass_value
    map_row["risk_display"] = f"{biomass_value:,.0f} lb/ac"
    st.plotly_chart(build_map(map_row, "Cereal Rye Biomass"), use_container_width=True)

    # 8. Daily detail (handy for spotting bad GDD / precip days).
    with st.expander("Daily breakdown", expanded=False):
        st.dataframe(ts, use_container_width=True)

    with st.expander("Raw wiscopy weather (long-format)", expanded=False):
        st.write("Columns:", list(weather.columns))
        st.dataframe(weather.head(50), use_container_width=True)

    st.caption(f"Last observed: {pd.Timestamp(last_obs).date()}  ·  "
               f"loaded {datetime.now().strftime('%Y-%m-%d %H:%M')}")


def main() -> None:
    """Top-level page composition: sidebar → three content tabs."""
    selected_date, risk_days, disease_label = sidebar_controls()

    forecast_tab, trends_tab, weather_tab = st.tabs([
        "🌽 Disease Forecast",
        "📈 Risk Trends",
        "🌤 Weather Data",
    ])
    opts = DISEASE_OPTIONS[disease_label]
    with forecast_tab:
        if opts.get("type") == "biomass":
            render_biomass_forecast_tab(selected_date, opts.get("model_name", ""))
        else:
            render_forecast_tab(selected_date, risk_days, disease_label)
    with trends_tab:
        # Risk Trends only applies to disease models — biomass has its own
        # time-series view inside the Forecast tab map (planting → today).
        if opts.get("type") == "biomass":
            st.info("Risk Trends is only available for disease models. "
                    "Select a disease in the sidebar to use this tab.")
        else:
            render_risk_trends_tab(selected_date, disease_label)
    with weather_tab:
        render_weather_tab()


if __name__ == "__main__":
    main()
