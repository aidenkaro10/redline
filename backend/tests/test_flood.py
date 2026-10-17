"""Tests for river gauges and tide stations. No internet."""

from datetime import timedelta

from backend.hazards import flood, store
from backend.hazards.events import iso, parse_time
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site, load_sites

NOW = parse_time("2026-10-17T12:00:00Z")
SITE = get_site("eia-136")
GAUGE = {"lid": "ASTF1", "name": "St. Johns River at Astor", "distance_mi": 5.0,
         "stages": {"action": 2, "minor": 2.3, "moderate": 3, "major": 4}, "units": "ft"}
STATION = {"id": "8720218", "name": "Mayport", "distance_mi": 3.0,
           "flood_levels_ft_stnd": {"minor": 15.83, "moderate": 16.33, "major": 17.33}}
CTX = {SITE.site_id: {"gauges": GAUGE, "tides": STATION}}


def status(obs_cat, obs_stage, fc_cat="fcst_not_current", fc_stage=-999, fc_hours=None):
    fc_time = iso(NOW + timedelta(hours=fc_hours)) if fc_hours else "0001-01-01T00:00:00Z"
    return {"ASTF1": {"observed": {"primary": obs_stage, "floodCategory": obs_cat, "validTime": iso(NOW)},
                      "forecast": {"primary": fc_stage, "floodCategory": fc_cat, "validTime": fc_time}}}


def test_minor_flood_now_and_moderate_forecast(monkeypatch):
    # Like the live Astor reading on Oct 17: minor now, moderate forecast
    monkeypatch.setattr(flood, "fetch_gauge_status", lambda: status("minor", 2.51, "moderate", 3.3, 30))
    monkeypatch.setattr(flood, "fetch_water_levels", lambda ids: {})
    events, info, _ = flood.flood_events([SITE], CTX, now=NOW)
    by_id = {e.source_id: e for e in events}
    assert by_id["ASTF1-obs"].severity == "warning" and by_id["ASTF1-obs"].pattern == "FLOOD_FIRE"
    assert by_id["ASTF1-fcst"].severity == "advisory" and by_id["ASTF1-fcst"].pattern is None
    assert info[SITE.site_id]["river"]["observed"]["primary"] == 2.51


def test_far_forecast_is_info_and_normal_river_is_quiet(monkeypatch):
    monkeypatch.setattr(flood, "fetch_gauge_status", lambda: status("no_flooding", 1.0, "minor", 2.4, 80))
    monkeypatch.setattr(flood, "fetch_water_levels", lambda ids: {})
    events, _, _ = flood.flood_events([SITE], CTX, now=NOW)
    assert [(e.source_id, e.severity) for e in events] == [("ASTF1-fcst", "info")]


def test_coastal_flooding_from_tide_station(monkeypatch):
    monkeypatch.setattr(flood, "fetch_gauge_status", lambda: {})
    monkeypatch.setattr(flood, "fetch_water_levels",
                        lambda ids: {"8720218": {"t": iso(NOW), "ft_stnd": 16.5, "preliminary": True}})
    events, _, _ = flood.flood_events([SITE], CTX, now=NOW)
    assert [(e.hazard, e.severity) for e in events] == [("flood", "danger")]    # above moderate


def test_flood_feeds_survive_wifi_drop(monkeypatch):
    monkeypatch.setattr(flood, "fetch_gauge_status", lambda: status("minor", 2.51))
    monkeypatch.setattr(flood, "fetch_water_levels", lambda ids: {})
    flood.flood_events([SITE], CTX, now=NOW)

    def offline(*a):
        raise OSError("WiFi down")
    monkeypatch.setattr(flood, "fetch_gauge_status", offline)
    monkeypatch.setattr(flood, "fetch_water_levels", offline)
    events, _, feeds = flood.flood_events([SITE], CTX, now=NOW + timedelta(hours=1))
    assert feeds["gauges"].stale and events and events[0].stale


def test_fake_mode_floods_the_st_johns_site():
    store.set_mode("fake")
    events, _, _ = flood.flood_events(list(load_sites()), now=NOW)
    assert any(e.site_id == "eia-136" and e.severity == "warning" for e in events)


def test_every_site_context_entry_has_sane_gauge_and_tide_data():
    from backend.config import RIVER_GAUGE_MAX_DIST_MI, TIDE_STATION_MAX_DIST_MI
    from backend.sites.build_context import load_context
    ctx = load_context()
    gauges = [v["gauges"] for v in ctx.values() if v.get("gauges")]
    tides = [v["tides"] for v in ctx.values() if v.get("tides")]
    assert len(gauges) >= 50 and len(tides) >= 5
    assert all(g["distance_mi"] <= RIVER_GAUGE_MAX_DIST_MI and "minor" in g["stages"] for g in gauges)
    assert all(t["distance_mi"] <= TIDE_STATION_MAX_DIST_MI for t in tides)
