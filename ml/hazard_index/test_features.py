"""
Leakage guard: features for day D may only use data known by 6 PM on day D-1.

The main test builds features, then scrambles EVERYTHING that happened after a
day's cutoff (weather, warnings, storm reports, outlooks), rebuilds, and checks
that day's features didn't move. If someone adds a feature that peeks at the
next day, this test fails.
"""

import datetime as dt

import numpy as np
import pandas as pd

from ml.hazard_index import features as F
from ml.hazard_index.sites import Site

SITE = Site("gnv", "Gainesville", 29.65, -82.32)
FACTS = dict(flood_zone_score=3, levee=0, gauge_km=12.0)
DAYS = [dt.date(2024, 6, d) for d in range(3, 12)]


def fake_inputs(seed: int):
    rng = np.random.default_rng(seed)
    times = pd.date_range("2024-05-20", "2024-06-15", freq="h", tz="UTC")
    n = len(times)
    weather = pd.DataFrame(dict(
        time=times, temperature_2m=rng.uniform(22, 36, n), dew_point_2m=rng.uniform(18, 26, n),
        relative_humidity_2m=rng.uniform(40, 100, n), precipitation=rng.exponential(0.5, n),
        wind_gusts_10m=rng.uniform(5, 60, n), soil_moisture_0_to_7cm=rng.uniform(0.1, 0.4, n),
        soil_moisture_7_to_28cm=rng.uniform(0.1, 0.4, n)))
    warnings, lsrs, spc = [], [], []
    for _ in range(40):
        start = times[rng.integers(0, n - 10)].to_pydatetime()
        end = start + dt.timedelta(hours=int(rng.integers(1, 60)))
        ph, sig = ["SV", "HT", "FA", "FW", "TR"][rng.integers(5)], "WYA"[rng.integers(3)]
        warnings.append(dict(phenomena=ph, significance=sig, issue=start.isoformat(),
                             expire=end.isoformat(), ph_name=ph, sig_name=sig, ugc="FLC001"))
        lsrs.append(dict(valid=times[rng.integers(0, n)].isoformat(), typetext="HAIL"))
        valid_day = DAYS[rng.integers(len(DAYS))]
        valid = dt.datetime(valid_day.year, valid_day.month, valid_day.day, 12, tzinfo=dt.timezone.utc)
        spc.append(dict(kind=["cat", "fire"][rng.integers(2)], level=int(rng.integers(1, 4)),
                        valid=valid, issued=valid - dt.timedelta(hours=int(rng.integers(10, 40)))))
    return weather, warnings, lsrs, spc


def scramble_after(cut: dt.datetime, weather, warnings, lsrs, spc, seed: int):
    """Replace or add random data for everything strictly after `cut`."""
    rng = np.random.default_rng(seed)
    w = weather.copy()
    late = w["time"] > pd.Timestamp(cut)
    for col in w.columns[1:]:
        w.loc[late, col] = rng.uniform(0, 100, late.sum()) if col != "precipitation" else 500.0
    t0 = cut + dt.timedelta(minutes=1)
    extra_w = [dict(phenomena="EH", significance="W", issue=t0.isoformat(),
                    expire=(t0 + dt.timedelta(days=3)).isoformat(), ph_name="x", sig_name="x", ugc="FLC001")]
    extra_l = [dict(valid=t0.isoformat(), typetext="TORNADO")] * 5
    spc2 = [dict(o, level=6) if o["issued"] > cut else o for o in spc]
    spc2 += [dict(kind=k, level=6, issued=t0, valid=dict_valid) for k in ("cat", "fire")
             for dict_valid in [dt.datetime.combine(d, dt.time(12), dt.timezone.utc) for d in DAYS]]
    return w, warnings + extra_w, lsrs + extra_l, spc2


def test_no_feature_uses_data_after_the_cutoff():
    weather, warnings, lsrs, spc = fake_inputs(seed=1)
    base = F.build_site_features(SITE, DAYS, weather, warnings, lsrs, spc, FACTS)
    for i, day in enumerate(DAYS):
        cut = F.cutoff_utc(day, SITE.timezone)
        w2, wa2, l2, s2 = scramble_after(cut, weather, warnings, lsrs, spc, seed=100 + i)
        again = F.build_site_features(SITE, DAYS, w2, wa2, l2, s2, FACTS)
        a, b = base.loc[i, F.FEATURES], again.loc[i, F.FEATURES]
        changed = [f for f in F.FEATURES if not (np.isclose(a[f], b[f]) or (pd.isna(a[f]) and pd.isna(b[f])))]
        assert not changed, f"features for {day} used data after the cutoff {cut}: {changed}"


def test_scrambling_does_change_later_days():
    """Sanity check that the test above has teeth: data after the cutoff DOES reach later days."""
    weather, warnings, lsrs, spc = fake_inputs(seed=2)
    base = F.build_site_features(SITE, DAYS, weather, warnings, lsrs, spc, FACTS)
    cut = F.cutoff_utc(DAYS[2], SITE.timezone)
    w2, wa2, l2, s2 = scramble_after(cut, weather, warnings, lsrs, spc, seed=7)
    again = F.build_site_features(SITE, DAYS, w2, wa2, l2, s2, FACTS)
    assert again.loc[3, "rain_24h"] > base.loc[3, "rain_24h"] + 100
    assert again.loc[3, "nws_now_heat"] == 5 and again.loc[3, "lsr_24h"] >= 5


def test_cutoff_is_6pm_local_the_day_before():
    assert F.cutoff_utc(dt.date(2024, 6, 6), "America/New_York") == dt.datetime(2024, 6, 5, 22, tzinfo=dt.timezone.utc)
    assert F.cutoff_utc(dt.date(2024, 1, 6), "America/Chicago") == dt.datetime(2024, 1, 6, 0, tzinfo=dt.timezone.utc)


def test_heat_index_matches_nws_table():
    # NWS chart: 96 °F at 65% humidity -> heat index about 121 °F; 80 °F at 40% -> about 80 °F
    assert abs(F.heat_index_f(np.array([(96 - 32) * 5 / 9]), np.array([65.0]))[0] - 121) < 2
    assert abs(F.heat_index_f(np.array([(80 - 32) * 5 / 9]), np.array([40.0]))[0] - 80) < 2
