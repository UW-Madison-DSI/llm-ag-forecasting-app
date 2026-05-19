"""Cereal rye biomass prediction model.

Direct port of the NLS (nonlinear least squares) model fitted in R by
Ben Bradford (2026-05-15). Predicts end-of-fall biomass from three
pre-winter agronomic inputs:

- planting day of year (DOY)
- accumulated precipitation between planting and Dec 31 (mm)
- cumulative sine growing-degree-days, base 0 °C

The functional form is a logistic-modulated linear combination::

    raw = (b0 + b_pd * plant_doy + b_pf * precip_fall)
          / (1 + exp(-k * (gdd_total - x0)))
    biomass = raw ** 2

Coefficients come straight from the original R fit, so predictions
are on the same scale / units as that model.

Original R reference::

    predict_rye_biomass <- function(plant_doy, precip_fall, gdd_total) {
      b0 <- 4.231e+02
      b_pd <- -1.031e+00
      b_pf <- -2.878e-01
      k <- 3.663e-03
      x0 <- 1.049e+03
      pred <- (b0 + b_pd * plant_doy + b_pf * precip_fall) /
              (1 + exp(-k * (gdd_total - x0)))
      pred^2
    }
"""

from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

# Coefficients from the original NLS fit.
_B0 = 4.231e+02     # intercept
_B_PD = -1.031e+00  # slope on planting DOY
_B_PF = -2.878e-01  # slope on fall precipitation (mm)
_K = 3.663e-03      # logistic steepness
_X0 = 1.049e+03     # logistic midpoint (GDD)

Number = Union[float, int, np.ndarray, pd.Series]


def predict_rye_biomass(
    plant_doy: Number,
    precip_fall: Number,
    gdd_total: Number,
) -> np.ndarray:
    """Predict cereal rye biomass from pre-winter agronomic inputs.

    All three inputs broadcast together — pass scalars, numpy arrays,
    or pandas Series interchangeably (e.g. scalar planting date with a
    Series of daily GDD totals to plot biomass accumulation over time).

    Args:
        plant_doy: Planting day of year (typically 250–300 for fall seeding).
        precip_fall: Precipitation (mm) between planting date and Dec 31.
        gdd_total: Cumulative sine GDD, base 0 °C.

    Returns:
        Predicted biomass on the original NLS scale, in the broadcast
        shape of the inputs. Units follow the R fit (typically lb/acre).
    """
    plant_doy = np.asarray(plant_doy, dtype=float)
    precip_fall = np.asarray(precip_fall, dtype=float)
    gdd_total = np.asarray(gdd_total, dtype=float)

    numerator = _B0 + _B_PD * plant_doy + _B_PF * precip_fall
    denominator = 1.0 + np.exp(-_K * (gdd_total - _X0))
    raw = numerator / denominator
    return raw ** 2


# ---------------------------------------------------------------------------
# Helpers for going from raw wiscopy weather records to the model's inputs.
# ---------------------------------------------------------------------------


def fahrenheit_to_celsius(f: Number) -> np.ndarray:
    """Vectorized °F → °C conversion."""
    return (np.asarray(f, dtype=float) - 32.0) * 5.0 / 9.0


def inches_to_mm(inches: Number) -> np.ndarray:
    """Vectorized inches → millimetres conversion."""
    return np.asarray(inches, dtype=float) * 25.4


def sine_gdd(tmax_c: Number, tmin_c: Number, base: float = 0.0) -> np.ndarray:
    """Single-sine daily growing-degree-days (Baskerville–Emin 1969).

    Integrates a sine approximation of the daily temperature trace
    between ``tmin_c`` and ``tmax_c`` above ``base``. Vectorized over
    array-like inputs.
    """
    tmax = np.asarray(tmax_c, dtype=float)
    tmin = np.asarray(tmin_c, dtype=float)

    gdd = np.zeros_like(tmax)

    # Full day at or above the base temperature → simple average.
    full = tmin >= base
    gdd = np.where(full, (tmax + tmin) / 2.0 - base, gdd)

    # Mixed day (tmin < base < tmax) → Baskerville–Emin closed form.
    mixed = (tmin < base) & (tmax > base)
    if np.any(mixed):
        avg = (tmax + tmin) / 2.0
        half_range = (tmax - tmin) / 2.0
        # Guard against zero range and arcsin domain.
        safe_hr = np.where(half_range == 0, 1.0, half_range)
        ratio = np.clip((base - avg) / safe_hr, -1.0, 1.0)
        theta = np.arcsin(ratio)
        mixed_gdd = (
            half_range * np.cos(theta) + (avg - base) * (np.pi / 2.0 - theta)
        ) / np.pi
        gdd = np.where(mixed, mixed_gdd, gdd)

    return np.maximum(gdd, 0.0)


def _find_field_column(columns) -> str:
    """Locate the column in wiscopy long-format that holds the field name."""
    for candidate in ("standard_name", "fieldname", "field"):
        if candidate in columns:
            return candidate
    raise ValueError(
        "wiscopy DataFrame must include a 'standard_name', 'fieldname', or 'field' column."
    )


def _find_time_column(df: pd.DataFrame) -> str:
    """Locate the timestamp column in wiscopy long-format."""
    for candidate in ("collection_time", "time", "timestamp", "date"):
        if candidate in df.columns:
            return candidate
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    # Fall back to whatever was the index.
    return df.columns[0]


def biomass_timeseries(
    weather_long: pd.DataFrame,
    plant_date,
    temp_field: str,
    precip_field: str | None = None,
    fall_precip_mm: float | None = None,
) -> pd.DataFrame:
    """Compute the daily biomass forecast from wiscopy long-format weather.

    Pipeline:
        1. Pivot wiscopy long → wide (one column per field).
        2. Group by calendar date to get daily Tavg (handles both
           ``daily_*`` fields and sub-daily ``60min_*`` fields).
        3. Convert °F → °C and compute daily GDD as ``max(0, Tavg_c)``
           (simple-average method, base 0 °C). When only a daily
           average is available we can't run the proper sine method,
           so we fall back to this — predictions stay close enough.
        4. Cumulative GDD (and cumulative precip in mm if a precip
           field is given) from planting date onward.
        5. Run :func:`predict_rye_biomass` per day. ``precip_fall``
           uses the running cumulative precip if available, else the
           ``fall_precip_mm`` fallback (constant), else 0.

    Args:
        weather_long: DataFrame from ``features.weather.fetch_weather_data``.
        plant_date: ``datetime.date`` / ``pd.Timestamp``.
        temp_field: wiscopy field name for air temperature in °F.
        precip_field: wiscopy field name for daily precipitation in inches.
            Optional — if missing or absent from the data, falls back to
            ``fall_precip_mm``.
        fall_precip_mm: Fallback total precip (mm) when no precip field
            is in the data.

    Returns:
        Daily-indexed DataFrame with columns ``tavg_c``, ``gdd_day``,
        ``gdd_total``, ``precip_mm``, ``precip_total_mm``, ``biomass_pred``.
        Empty if inputs are insufficient.
    """
    if weather_long is None or weather_long.empty:
        return pd.DataFrame()

    df = weather_long.copy()
    if df.index.name:
        df = df.reset_index()

    field_col = _find_field_column(df.columns)
    if "value" not in df.columns:
        raise ValueError("wiscopy DataFrame must include a 'value' column.")

    time_col = _find_time_column(df)
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    df = df.dropna(subset=[time_col])

    wide = df.pivot_table(index=time_col, columns=field_col, values="value", aggfunc="mean")
    if temp_field not in wide.columns:
        raise ValueError(
            f"Expected wiscopy field '{temp_field}' in the response — got "
            f"{list(wide.columns)}. Update BIOMASS_TEMP_FIELD in features/config.py."
        )

    # Daily Tavg (°F) — works for both daily_* and sub-daily fields.
    by_date = wide[temp_field].groupby(wide.index.normalize())
    daily = pd.DataFrame({"tavg_f": by_date.mean()})
    daily.index = pd.DatetimeIndex(daily.index)

    plant_ts = pd.Timestamp(plant_date).normalize()
    daily = daily[daily.index >= plant_ts]
    if daily.empty:
        return daily

    daily["tavg_c"] = fahrenheit_to_celsius(daily["tavg_f"])
    daily["gdd_day"] = np.maximum(daily["tavg_c"].fillna(0.0), 0.0)
    daily["gdd_total"] = daily["gdd_day"].cumsum()

    # Precip: prefer real data when present, else constant fallback.
    if precip_field and precip_field in wide.columns:
        precip_in = wide[precip_field].groupby(wide.index.normalize()).sum()
        precip_in.index = pd.DatetimeIndex(precip_in.index)
        precip_in = precip_in.reindex(daily.index, fill_value=0.0)
        daily["precip_mm"] = inches_to_mm(precip_in)
        daily["precip_total_mm"] = daily["precip_mm"].fillna(0.0).cumsum()
        precip_for_model = daily["precip_total_mm"].values
    elif fall_precip_mm is not None:
        daily["precip_mm"] = 0.0
        daily["precip_total_mm"] = float(fall_precip_mm)
        precip_for_model = float(fall_precip_mm)
    else:
        daily["precip_mm"] = 0.0
        daily["precip_total_mm"] = 0.0
        precip_for_model = 0.0

    plant_doy = plant_ts.timetuple().tm_yday
    daily["biomass_pred"] = predict_rye_biomass(
        plant_doy, precip_for_model, daily["gdd_total"].values
    )
    return daily


def biomass_per_station(
    weather_long: pd.DataFrame,
    plant_date,
    temp_field: str,
    precip_field: str | None = None,
    fall_precip_mm: float | None = None,
) -> pd.DataFrame:
    """Compute the final biomass prediction for each station in the frame.

    Splits ``weather_long`` by ``station_id`` and runs
    :func:`biomass_timeseries` for each group, then keeps only the
    last row's accumulated values (the prediction "as of" the latest
    observation date per station).

    Returns:
        DataFrame with one row per station — columns ``station_id``,
        ``biomass_pred``, ``gdd_total``, ``precip_total_mm``,
        ``last_observed``.
    """
    if weather_long is None or weather_long.empty:
        return pd.DataFrame(columns=["station_id", "biomass_pred", "gdd_total", "precip_total_mm", "last_observed"])

    df = weather_long.copy()
    if df.index.name:
        df = df.reset_index()
    if "station_id" not in df.columns:
        raise ValueError("wiscopy DataFrame must include a 'station_id' column.")

    rows = []
    for sid, group in df.groupby("station_id"):
        try:
            ts = biomass_timeseries(group, plant_date, temp_field, precip_field, fall_precip_mm)
        except ValueError:
            continue
        if ts.empty:
            continue
        last = ts.iloc[-1]
        rows.append({
            "station_id": sid,
            "biomass_pred": float(last["biomass_pred"]),
            "gdd_total": float(last["gdd_total"]),
            "precip_total_mm": float(last.get("precip_total_mm", 0.0)),
            "last_observed": ts.index[-1],
        })
    return pd.DataFrame(rows)


def classify_biomass(value, low_max: float, high_min: float) -> str:
    """Bucket a biomass value (lb/ac) into Low / Moderate / High / Unknown."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "Unknown"
    if value < low_max:
        return "Low"
    if value < high_min:
        return "Moderate"
    return "High"
