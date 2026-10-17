"""Tests for the lightning stop-work rule, the GLM reader and demo strikes. No internet."""

import math
from datetime import timedelta

import numpy as np
import pytest

from backend.hazards import lightning, store
from backend.hazards.events import parse_time
from backend.hazards.inject import clear_injected, inject_test_event
from backend.hazards.lightning import key_start_time, site_lightning_state
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site, load_sites

T0 = parse_time("2026-07-20T20:00:00Z")
GNV = get_site("eia-663")


def flash(minutes, miles_east, q=0, site=GNV):
    """A flash `minutes` after T0, `miles_east` of the site."""
    dlon = miles_east / (69.0 * math.cos(math.radians(site.lat)))
    return {"t": (T0 + timedelta(minutes=minutes)).timestamp(), "lat": site.lat,
            "lon": site.lon + dlon, "q": q}


def state(flashes, at_min, feed_ok_min=None):
    now = T0 + timedelta(minutes=at_min)
    ok = now if feed_ok_min is None else T0 + timedelta(minutes=feed_ok_min)
    return site_lightning_state(GNV, flashes, now, ok)


def test_one_flash_is_not_enough():
    st = state([flash(0, 5)], 1)
    assert st["state"] == "unconfirmed" and st["flashes_10mi_30min"] == 1


def test_two_flashes_within_10_miles_stop_work():
    st = state([flash(0, 5), flash(3, 8)], 4)
    assert st["state"] == "stop_work"
    assert st["minutes_to_all_clear"] == pytest.approx(29.0)
    assert st["all_clear_at"] == "2026-07-20T20:33:00Z"


def test_each_new_strike_resets_the_countdown():
    flashes = [flash(0, 5), flash(3, 8), flash(25, 9)]
    st = state(flashes, 40)
    assert st["state"] == "stop_work" and st["minutes_to_all_clear"] == pytest.approx(15.0)
    assert state(flashes, 54.9)["state"] == "stop_work"
    assert state(flashes, 55.1)["state"] == "clear"           # 30 quiet minutes after minute 25


def test_bad_quality_and_far_flashes_dont_stop_work():
    assert state([flash(0, 5, q=3), flash(1, 5, q=3)], 2)["state"] == "clear"
    st = state([flash(0, 12), flash(1, 15)], 2)
    assert st["state"] == "nearby" and st["nearest_mi_30min"] == pytest.approx(12, abs=0.1)


def test_flashes_too_far_apart_in_time_dont_confirm():
    st = state([flash(0, 5), flash(31, 5)], 32)
    assert st["state"] == "unconfirmed"


def test_no_all_clear_while_feed_is_down():
    flashes = [flash(0, 5), flash(1, 5)]
    # The feed died at minute 10; at minute 40 the countdown has "run out" but we never saw it
    st = state(flashes, 40, feed_ok_min=10)
    assert st["state"] == "stop_work" and st["all_clear_unconfirmed"]
    # Feed back up at minute 40: all clear
    assert state(flashes, 40, feed_ok_min=40)["state"] == "clear"


def test_key_start_time():
    k = "GLM-L2-LCFA/2026/290/11/OR_GLM-L2-LCFA_G19_s20262901133400_e20262901134000_c20262901134017.nc"
    assert key_start_time(k) == parse_time("2026-10-17T11:33:40Z")


def test_parse_glm_reads_scaled_netcdf(tmp_path):
    netCDF4 = pytest.importorskip("netCDF4")
    path = tmp_path / "glm.nc"
    ds = netCDF4.Dataset(path, "w")
    ds.createDimension("number_of_flashes", 3)
    for name, vals in [("flash_lat", [29.76, 40.0, 29.70]), ("flash_lon", [-82.30, -100.0, -82.40])]:
        ds.createVariable(name, "f4", ("number_of_flashes",))[:] = vals
    q = ds.createVariable("flash_quality_flag", "i2", ("number_of_flashes",))
    q[:] = [0, 0, 3]
    e = ds.createVariable("flash_energy", "f4", ("number_of_flashes",))
    e[:] = [1e-13, 1e-13, 1e-13]
    t = ds.createVariable("flash_time_offset_of_first_event", "i2", ("number_of_flashes",))
    t.scale_factor, t.add_offset = np.float32(0.0003814756), np.float32(-5.0)
    t.units = "seconds since 2026-10-17 11:33:40.000"
    t[:] = [1.0, 2.0, -1.0]
    ds.close()
    out = lightning.parse_glm(path.read_bytes())
    assert len(out) == 2                              # the Texas one is outside Florida
    assert out[0]["t"] == pytest.approx(parse_time("2026-10-17T11:33:41Z").timestamp(), abs=0.01)
    assert [f["q"] for f in out] == [0, 3]


def test_glm_feed_offline_is_stale(monkeypatch):
    def offline(*a, **k):
        raise OSError("WiFi down")
    monkeypatch.setattr(lightning, "list_glm_keys", offline)
    feed = lightning.GLMFeed()
    feed.update(T0)
    r = feed.result(T0)
    assert r.stale and "WiFi down" in r.error


def test_injected_strikes_start_stop_work_and_countdown():
    store.set_mode("fake")
    inject_test_event(GNV.site_id, "lightning", distance_mi=4, now=T0)
    events, states, _ = lightning.lightning_events([GNV], now=T0 + timedelta(minutes=1))
    assert states[GNV.site_id]["state"] == "stop_work"
    assert events[0].pattern == "LIGHTNING" and events[0].severity == "danger"
    _, states, _ = lightning.lightning_events([GNV], now=T0 + timedelta(minutes=31))
    assert states[GNV.site_id]["state"] == "clear"
    clear_injected()


def test_fake_storm_cycle_at_crystal_river():
    store.set_mode("fake")
    sites = list(load_sites())
    hour = T0.replace(minute=0)
    _, st, _ = lightning.lightning_events(sites, now=hour + timedelta(minutes=12))
    assert st["eia-628"]["state"] == "stop_work"
    events, st, _ = lightning.lightning_events(sites, now=hour + timedelta(minutes=40))
    cr = [e for e in events if e.site_id == "eia-628"][0]
    assert cr.status == "all_clear_pending" and cr.pattern == "LIGHTNING"
    _, st, _ = lightning.lightning_events(sites, now=hour + timedelta(minutes=55))
    assert st["eia-628"]["state"] == "clear"
    assert st["eia-663"]["state"] == "clear"          # Gainesville is out of the storm
