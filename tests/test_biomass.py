"""Tests for features/crereal_rye_biomass.py — the NLS model + helpers.

The reference values come from running the original R fit::

    predict_rye_biomass <- function(plant_doy, precip_fall, gdd_total) {
      b0 <- 4.231e+02; b_pd <- -1.031e+00; b_pf <- -2.878e-01
      k  <- 3.663e-03; x0   <- 1.049e+03
      pred <- (b0 + b_pd * plant_doy + b_pf * precip_fall) /
              (1 + exp(-k * (gdd_total - x0)))
      pred^2
    }
"""

import math

import numpy as np
import pytest

from features.crereal_rye_biomass import (
    classify_biomass,
    fahrenheit_to_celsius,
    inches_to_mm,
    predict_rye_biomass,
    sine_gdd,
)


# ---------------------------------------------------------------------------
# Unit converters — exact arithmetic.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f, c", [
    (32.0, 0.0),
    (212.0, 100.0),
    (-40.0, -40.0),
])
def test_fahrenheit_to_celsius(f, c):
    assert fahrenheit_to_celsius(f) == pytest.approx(c, abs=1e-9)


def test_fahrenheit_to_celsius_array():
    out = fahrenheit_to_celsius(np.array([32.0, 50.0, 86.0]))
    assert out == pytest.approx([0.0, 10.0, 30.0])


def test_inches_to_mm_scalar_and_array():
    assert inches_to_mm(1.0) == pytest.approx(25.4)
    assert inches_to_mm(np.array([0.0, 1.0, 2.5])) == pytest.approx([0.0, 25.4, 63.5])


# ---------------------------------------------------------------------------
# sine_gdd — three regimes (full warm day / mixed / cold).
# ---------------------------------------------------------------------------

def test_sine_gdd_full_warm_day_above_base():
    # tmin >= base → simple average above base.
    gdd = sine_gdd(tmax_c=20.0, tmin_c=10.0, base=0.0)
    assert gdd == pytest.approx(15.0)


def test_sine_gdd_full_cold_day_below_base_is_zero():
    gdd = sine_gdd(tmax_c=-5.0, tmin_c=-10.0, base=0.0)
    assert gdd == pytest.approx(0.0)


def _baskerville_emin(tmax, tmin, base=0.0):
    """Closed-form reference for the mixed-day case (tmin < base < tmax)."""
    avg = (tmax + tmin) / 2.0
    half_range = (tmax - tmin) / 2.0
    theta = math.asin((base - avg) / half_range)
    return (half_range * math.cos(theta) + (avg - base) * (math.pi / 2.0 - theta)) / math.pi


def test_sine_gdd_mixed_day_uses_baskerville_emin():
    # tmin < base < tmax → use the closed-form Baskerville–Emin value.
    gdd = sine_gdd(tmax_c=10.0, tmin_c=-2.0, base=0.0)
    assert gdd == pytest.approx(_baskerville_emin(10.0, -2.0), rel=1e-9)


def test_sine_gdd_array_broadcasts():
    out = sine_gdd(
        tmax_c=np.array([20.0, -5.0, 10.0]),
        tmin_c=np.array([10.0, -10.0, -2.0]),
        base=0.0,
    )
    assert out[0] == pytest.approx(15.0)
    assert out[1] == pytest.approx(0.0)
    assert out[2] == pytest.approx(_baskerville_emin(10.0, -2.0), rel=1e-9)


# ---------------------------------------------------------------------------
# predict_rye_biomass — verified against the R reference numerically.
# ---------------------------------------------------------------------------

def _reference(plant_doy, precip_fall, gdd_total):
    """Direct transcription of the R reference."""
    b0, b_pd, b_pf, k, x0 = 423.1, -1.031, -0.2878, 0.003663, 1049.0
    num = b0 + b_pd * plant_doy + b_pf * precip_fall
    den = 1.0 + math.exp(-k * (gdd_total - x0))
    return (num / den) ** 2


@pytest.mark.parametrize("doy, precip, gdd", [
    (258, 200.0,  500.0),    # typical fall seeding, low GDD
    (258, 200.0, 1200.0),    # past midpoint
    (270, 350.0, 1500.0),    # later seeding, more precip
    (250,   0.0, 1000.0),    # near midpoint with no precip
])
def test_predict_rye_biomass_matches_r_reference(doy, precip, gdd):
    expected = _reference(doy, precip, gdd)
    actual = float(predict_rye_biomass(doy, precip, gdd))
    assert actual == pytest.approx(expected, rel=1e-9)


def test_predict_rye_biomass_broadcasts_over_arrays():
    out = predict_rye_biomass(
        plant_doy=258,
        precip_fall=200.0,
        gdd_total=np.array([500.0, 1000.0, 1500.0]),
    )
    assert out.shape == (3,)
    # Logistic in GDD → strictly increasing
    assert out[0] < out[1] < out[2]


# ---------------------------------------------------------------------------
# classify_biomass — bucket thresholds.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0.0,    "Low"),
    (499.9,  "Low"),
    (500.0,  "Moderate"),
    (1000.0, "Moderate"),
    (1499.9, "Moderate"),
    (1500.0, "High"),
    (5000.0, "High"),
])
def test_classify_biomass_thresholds(value, expected):
    assert classify_biomass(value, low_max=500.0, high_min=1500.0) == expected


def test_classify_biomass_none_and_nan():
    assert classify_biomass(None, 500, 1500) == "Unknown"
    assert classify_biomass(float("nan"), 500, 1500) == "Unknown"
