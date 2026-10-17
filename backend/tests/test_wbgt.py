"""Tests for the Liljegren WBGT port, against published values and physics."""

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.hazards.wbgt import cos_zenith, esat, wbgt_flag, wbgt_from_weather, wbgt_liljegren


def stull_wet_bulb(t, rh):
    """Stull (2011, J. Appl. Meteor. Climatol. 50:2267) published wet-bulb formula, °C."""
    return (t * math.atan(0.151977 * (rh + 8.313659) ** 0.5) + math.atan(t + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
            - 4.686035)


def wmo_psychrometer_wet_bulb(t, rh, p=1013.25):
    """Wet bulb from the WMO aspirated-psychrometer equation (WMO-No. 8, Annex 4.B):
    e = es(Tw) - 6.53e-4 * (1 + 0.000944 Tw) * p * (T - Tw), solved by bisection."""
    es = lambda x: 6.112 * math.exp(17.62 * x / (243.12 + x))   # noqa: E731  (WMO Magnus form)
    e = rh / 100 * es(t)
    lo, hi = -20.0, float(t)
    for _ in range(60):
        mid = (lo + hi) / 2
        if es(mid) - 6.53e-4 * (1 + 0.000944 * mid) * p * (t - mid) > e:
            hi = mid
        else:
            lo = mid
    return mid


def test_stull_paper_example():
    # Stull's worked example: 20 °C and 50% RH -> 13.7 °C. Our psychrometric wick agrees.
    assert stull_wet_bulb(20, 50) == pytest.approx(13.7, abs=0.05)
    assert wbgt_liljegren(20, 50, 1013.25, 3.0, 0.0, 0.0)["tpsy"] == pytest.approx(13.7, abs=0.15)


@pytest.mark.parametrize("t,rh", [(20, 50), (25, 80), (30, 60), (35, 40), (33, 70)])
def test_psychrometric_wet_bulb_matches_wmo(t, rh):
    # With radiation switched off and some wind, the wick is a psychrometer.
    # Liljegren's wick reads 0.1-0.45 °C under WMO. (Stull's fit is off ~0.5 °C in hot dry air.)
    out = wbgt_liljegren(t, rh, 1013.25, 3.0, 0.0, 0.0)
    assert out["tpsy"] == pytest.approx(wmo_psychrometer_wet_bulb(t, rh), abs=0.5)


def test_saturation_vapor_pressure_buck():
    # Published: 31.7 hPa at 25 °C, 42.4 hPa at 30 °C (Buck 1981, x1.004 moist-air factor)
    assert esat(298.15) == pytest.approx(31.7 * 1.004, abs=0.15)
    assert esat(303.15) == pytest.approx(42.4 * 1.004, abs=0.2)


def test_florida_summer_noon_is_black_flag():
    # 33 °C, 55% RH, 2 m/s, strong sun: WBGT around 31-33 °C (88-91 °F), globe 15-25 °C above air
    out = wbgt_liljegren(33, 55, 1013, 2.0, 850, 0.95)
    assert 31.0 < out["wbgt"] < 33.5
    assert 15 < out["tg"] - 33 < 25
    assert out["tpsy"] < out["tnwb"] < 33      # sun warms the natural wet bulb above psychrometric


def test_shade_wbgt_is_below_sun_and_wind_cools_the_globe():
    sun = wbgt_liljegren(32, 60, 1013, 2.0, 800, 0.9)
    shade = wbgt_liljegren(32, 60, 1013, 2.0, 0, 0.9)
    windy = wbgt_liljegren(32, 60, 1013, 6.0, 800, 0.9)
    assert shade["wbgt"] < sun["wbgt"]
    assert windy["tg"] < sun["tg"]
    # No sun: globe sits within a degree or two of air temp
    assert abs(shade["tg"] - 32) < 2.0


def test_more_humidity_means_higher_wbgt():
    dry = wbgt_liljegren(33, 30, 1013, 2.0, 700, 0.8)["wbgt"]
    humid = wbgt_liljegren(33, 80, 1013, 2.0, 700, 0.8)["wbgt"]
    assert humid > dry + 3


def test_vectorized_matches_scalar():
    t = np.array([25.0, 30.0, 35.0])
    rh = np.array([80.0, 60.0, 40.0])
    solar = np.array([0.0, 500.0, 900.0])
    cza = np.array([0.0, 0.6, 0.9])
    vec = wbgt_liljegren(t, rh, 1013, 1.5, solar, cza)["wbgt"]
    for i in range(3):
        assert vec[i] == pytest.approx(wbgt_liljegren(t[i], rh[i], 1013, 1.5, solar[i], cza[i])["wbgt"])


def test_sun_position_gainesville():
    # Gainesville near solar noon on the June solstice: sun ~6° from overhead
    noon = cos_zenith(datetime(2026, 6, 21, 17, 30, tzinfo=timezone.utc), 29.65, -82.32)
    assert math.degrees(math.acos(noon)) == pytest.approx(29.65 - 23.44, abs=0.7)
    midnight = cos_zenith(datetime(2026, 6, 21, 5, 30, tzinfo=timezone.utc), 29.65, -82.32)
    assert midnight < 0


def test_from_weather_row_and_flags():
    out = wbgt_from_weather(datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc),
                            29.65, -82.32, 33, 55, 1013, 3.0, 800)
    assert out["wind_2m"] < 3.0                  # 10 m wind brought down to 2 m
    assert wbgt_flag(out["wbgt"]) in ("red", "black")
    assert wbgt_flag(25.0) == "none" and wbgt_flag(30.0) == "yellow"
