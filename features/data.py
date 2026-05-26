"""Transform the Wisconet API payload into tidy, plot-ready DataFrames.

The API returns a FeatureCollection-style object: each feature has a
``station`` block plus a ``timeseries`` list of daily records, where
each record carries a ``data`` array of ``{fieldname, value}`` pairs.
This module flattens that into one row per (station, date) with the
field names promoted to columns.
"""

import pandas as pd


def flatten_features(payload: dict) -> pd.DataFrame:
    """Pivot the FeatureCollection payload into one row per (station, date).

    Promotes every ``timeseries[].data[]`` ``fieldname`` to a column,
    drops stations missing lat/lon, and coerces coordinates to floats.

    Args:
        payload: Parsed JSON returned by ``features.api.fetch_forecast``.

    Returns:
        Flat DataFrame with station metadata + one column per risk
        field. Empty DataFrame if the payload has no features.
    """
    rows = []
    for feature in payload.get("features", []):
        station = feature.get("station", {}) or {}
        coords = station.get("coordinates", {}) or {}
        base = {
            "station_id": station.get("station_id"),
            "station_name": station.get("station_name"),
            "city": station.get("city"),
            "county": station.get("county"),
            "region": station.get("region"),
            "state": station.get("state"),
            "latitude": coords.get("latitude"),
            "longitude": coords.get("longitude"),
        }
        for ts in feature.get("timeseries", []) or []:
            row = {**base, "date": ts.get("date")}
            for item in ts.get("data", []) or []:
                row[item["fieldname"]] = item["value"]
            rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    return df.dropna(subset=["latitude", "longitude"])


def normalize_class(value) -> str:
    """Normalize a raw risk-class value to a stable Title-Case label.

    The API returns strings like ``"Low"``, ``"Moderate"``, ``"High"``,
    ``"Inactive"`` — but during the active season it often prefixes
    them with a sort key (``"1.Low"``, ``"2.Moderate"``, ``"3.High"``).
    Strip the prefix so the label matches ``CLASS_COLORS`` /
    ``CLASS_ORDER`` keys deterministically.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Unknown"
    import re
    text = re.sub(r"^\s*\d+\s*[.:)\-]\s*", "", str(value).strip())
    return text.title() if text else "Unknown"


def prepare_disease_df(df: pd.DataFrame, risk_field: str, class_field: str) -> pd.DataFrame:
    """Attach plot-ready columns for one selected disease model.

    Adds three columns derived from the user's chosen disease:

    - ``risk_class``  -- normalized label used to color markers
    - ``risk_value``  -- numeric score (may be NaN or the sentinel -1)
    - ``risk_display`` -- pretty string for hover (``"n/a"`` or ``"0.42"``)

    Missing fields are tolerated (filled with ``Unknown`` / ``None``)
    so out-of-season payloads don't break the UI.

    Args:
        df: Output of :func:`flatten_features`.
        risk_field: Name of the numeric risk column for the selected disease.
        class_field: Name of the discrete risk-class column for the selected disease.

    Returns:
        A copy of ``df`` with the three derived columns added.
    """
    out = df.copy()
    if class_field not in out.columns:
        out[class_field] = "Unknown"
    if risk_field not in out.columns:
        out[risk_field] = None

    out["risk_class"] = out[class_field].apply(normalize_class)
    out["risk_value"] = pd.to_numeric(out[risk_field], errors="coerce")
    # -1 is the API's sentinel for "model inactive for this station/date".
    out["risk_display"] = out["risk_value"].apply(
        lambda v: "n/a" if pd.isna(v) or v == -1 else f"{v:.2f}"
    )
    return out
