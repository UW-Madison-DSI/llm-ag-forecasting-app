"""Tests for streamlit_app/llm.py — pure helpers only.

The OpenAI call itself isn't tested here (it'd require either a network
call or a mock setup that's heavier than the function); the bug surface
worth covering is the context the model receives.
"""

import pandas as pd

from streamlit_app.llm import (
    DISEASE_REFERENCES,
    MAX_STATIONS_IN_CONTEXT,
    SUPPORTED_LANGUAGES,
    build_forecast_context,
    build_model_context,
    build_trends_context,
    reference_url_for,
    summarize_class_counts,
    system_prompt_for,
)


# ---------------------------------------------------------------------------
# reference_url_for
# ---------------------------------------------------------------------------

def test_reference_url_for_known_diseases():
    assert reference_url_for("Tar Spot (corn)").endswith("/tar-spot-of-corn")
    assert reference_url_for("Frogeye Leaf Spot (soybean)").endswith(
        "/frogeye-leaf-spot-of-soybean"
    )


def test_reference_url_for_unknown_disease():
    assert reference_url_for("Some Unknown Disease") is None


def test_all_three_white_mold_variants_share_one_reference():
    urls = {
        reference_url_for("White Mold — Non-irrigated (soybean)"),
        reference_url_for("White Mold — Irrigated 30in (soybean)"),
        reference_url_for("White Mold — Irrigated 15in (soybean)"),
    }
    assert urls == {DISEASE_REFERENCES["White Mold — Non-irrigated (soybean)"]}


# ---------------------------------------------------------------------------
# summarize_class_counts
# ---------------------------------------------------------------------------

def test_summarize_class_counts_basic():
    counts = summarize_class_counts(["High", "Low", "High", "Inactive", "Low", "Low"])
    assert counts == {"High": 2, "Low": 3, "Inactive": 1}


def test_summarize_class_counts_handles_none():
    counts = summarize_class_counts([None, "High", None])
    assert counts == {"Unknown": 2, "High": 1}


# ---------------------------------------------------------------------------
# build_forecast_context
# ---------------------------------------------------------------------------

def _make_df(rows):
    return pd.DataFrame(rows)


def test_build_forecast_context_includes_date_disease_reference():
    df = _make_df([
        {"station_name": "Arlington", "city": "Arlington", "county": "Columbia",
         "tarspot_risk": 0.42, "tarspot_risk_class": "High"},
    ])
    ctx = build_forecast_context(
        "2026-07-15", "Tar Spot (corn)", df, "tarspot_risk", "tarspot_risk_class"
    )
    assert "2026-07-15" in ctx
    assert "Tar Spot (corn)" in ctx
    assert "tar-spot-of-corn" in ctx
    assert "Arlington" in ctx
    assert "0.420" in ctx
    assert "Total stations: 1" in ctx


def test_build_forecast_context_includes_class_distribution():
    df = _make_df([
        {"station_name": f"S{i}",
         "tarspot_risk": 0.5 if i < 3 else -1,
         "tarspot_risk_class": "High" if i < 3 else "Inactive"}
        for i in range(5)
    ])
    ctx = build_forecast_context(
        "2026-07-15", "Tar Spot (corn)", df, "tarspot_risk", "tarspot_risk_class"
    )
    assert "Class distribution:" in ctx
    assert "High=3" in ctx
    assert "Inactive=2" in ctx


def test_build_forecast_context_excludes_inactive_stations_from_top_list():
    df = _make_df([
        {"station_name": "Active1", "tarspot_risk": 0.30, "tarspot_risk_class": "Moderate"},
        {"station_name": "InactiveX", "tarspot_risk": -1,   "tarspot_risk_class": "Inactive"},
        {"station_name": "Active2", "tarspot_risk": 0.70, "tarspot_risk_class": "High"},
    ])
    ctx = build_forecast_context(
        "2026-07-15", "Tar Spot (corn)", df, "tarspot_risk", "tarspot_risk_class"
    )
    assert "Active1" in ctx
    assert "Active2" in ctx
    assert "InactiveX" not in ctx
    # Descending order — Active2 (0.70) must come before Active1 (0.30).
    assert ctx.index("Active2") < ctx.index("Active1")


def test_build_forecast_context_caps_station_list_length():
    df = _make_df([
        {"station_name": f"S{i:03d}", "tarspot_risk": 0.01 * (100 - i),
         "tarspot_risk_class": "Moderate"}
        for i in range(MAX_STATIONS_IN_CONTEXT + 10)
    ])
    ctx = build_forecast_context(
        "2026-07-15", "Tar Spot (corn)", df, "tarspot_risk", "tarspot_risk_class"
    )
    # The top-N cap means the lowest-ranked stations don't appear.
    assert "S000" in ctx                      # rank 0 (highest risk)
    assert f"S{MAX_STATIONS_IN_CONTEXT - 1:03d}" in ctx
    assert f"S{MAX_STATIONS_IN_CONTEXT + 5:03d}" not in ctx


def test_build_forecast_context_handles_empty_df():
    ctx = build_forecast_context(
        "2026-07-15", "Tar Spot (corn)", pd.DataFrame(),
        "tarspot_risk", "tarspot_risk_class"
    )
    assert "No station data" in ctx
    # Still includes the date + disease so the model has anchors.
    assert "2026-07-15" in ctx
    assert "Tar Spot (corn)" in ctx


# ---------------------------------------------------------------------------
# build_trends_context
# ---------------------------------------------------------------------------

def _make_trends_df():
    """A small multi-day trends frame for two stations."""
    rows = []
    for sid, name in [("ALTN", "Arlington"), ("MAPL", "Maple")]:
        for i, (d, v, cls) in enumerate([
            ("2026-07-15", 0.10, "Low"),
            ("2026-07-16", 0.40, "Moderate"),
            ("2026-07-17", 0.75, "High"),
        ]):
            rows.append({
                "station_id": sid, "station_name": name,
                "plot_date": pd.Timestamp(d),
                "tarspot_risk": v if sid == "ALTN" else max(v - 0.3, -1),
                "tarspot_risk_class": cls if sid == "ALTN" else "Low",
            })
    return pd.DataFrame(rows)


def test_build_trends_context_includes_window_and_per_station_path():
    df = _make_trends_df()
    ctx = build_trends_context(
        "2026-07-17", "Tar Spot (corn)", df, "tarspot_risk", "tarspot_risk_class"
    )
    assert "Date window: 2026-07-15 → 2026-07-17 (3 day(s))" in ctx
    assert "Stations selected: 2" in ctx
    # Each station's trajectory uses the date=value[class] arrow format.
    assert "Arlington:" in ctx
    assert "Maple:" in ctx
    assert "→" in ctx                       # arrow between days
    assert "0.75[High]" in ctx              # last Arlington point


def test_build_trends_context_empty():
    ctx = build_trends_context(
        "2026-07-17", "Tar Spot (corn)", pd.DataFrame(),
        "tarspot_risk", "tarspot_risk_class"
    )
    assert "(No multi-day data available.)" in ctx
    assert "2026-07-17" in ctx
    assert "Tar Spot (corn)" in ctx


# ---------------------------------------------------------------------------
# build_model_context
# ---------------------------------------------------------------------------

def test_build_model_context_with_full_metadata():
    info = {
        "name": "tarspot", "crop": "corn", "version": "1.0",
        "description": "Tar spot risk model for corn.",
        "model_type": "logistic regression",
        "risk_output": "probability in [0, 1]",
        "inactive_rule": "VT growth stage not reached",
        "variables": ["mean_temp_30d", "rh_80_hours_14d"],
    }
    ctx = build_model_context("Tar Spot (corn)", "tarspot", info)
    assert "Tar Spot (corn)" in ctx
    assert "tar-spot-of-corn" in ctx          # reference URL
    assert "Crop: corn" in ctx
    assert "Version: 1.0" in ctx
    assert "Tar spot risk model for corn." in ctx
    assert "Model type: logistic regression" in ctx
    assert "Input variables: mean_temp_30d, rh_80_hours_14d" in ctx


def test_build_model_context_missing_metadata_still_anchors_disease():
    ctx = build_model_context("Tar Spot (corn)", "tarspot", None)
    assert "Tar Spot (corn)" in ctx
    assert "(No additional metadata returned by the API.)" in ctx


# ---------------------------------------------------------------------------
# system_prompt_for — language switching
# ---------------------------------------------------------------------------

def test_system_prompt_default_is_english():
    p = system_prompt_for()
    assert "Reply in English." in p
    # Always includes the no-recommendations policy.
    assert "MUST NOT" in p


def test_system_prompt_spanish():
    p = system_prompt_for("es")
    assert "Responde en español" in p
    # Policy is still present in the Spanish variant.
    assert "MUST NOT" in p


def test_system_prompt_unknown_lang_falls_back_to_english():
    p = system_prompt_for("klingon")
    assert "Reply in English." in p


def test_supported_languages_cover_en_and_es():
    assert set(SUPPORTED_LANGUAGES) == {"en", "es"}
