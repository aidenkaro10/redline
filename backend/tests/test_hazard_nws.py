"""Tests for NWS alerts and HeatRisk. No internet: fake mode and patched fetches."""

from datetime import timedelta

from backend.hazards import nws, store
from backend.hazards.events import iso, parse_time
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site, load_sites

T0 = parse_time("2026-07-20T18:00:00Z")
GNV = get_site("eia-663")          # Deerhaven, Gainesville: zones FLZ136 / FLC001


def alert(event, ugc=(), geometry=None, ends_h=2, vtec=()):
    return {"id": f"id-{event}", "event": event, "headline": event, "severity": "Severe",
            "urgency": "Immediate", "sent": iso(T0), "onset": iso(T0),
            "ends": iso(T0 + timedelta(hours=ends_h)), "sender": "NWS Jacksonville",
            "ugc": list(ugc), "vtec": list(vtec), "geometry": geometry, "instruction": ""}


def test_classify_maps_old_and_new_extreme_heat_names():
    assert nws.classify(alert("Excessive Heat Warning")) == ("heat", "danger")
    assert nws.classify(alert("Extreme Heat Warning")) == ("heat", "danger")
    assert nws.classify(alert("Heat Advisory")) == ("heat", "warning")
    assert nws.classify(alert("Rip Current Statement")) is None


def test_classify_falls_back_on_vtec_code():
    # An unknown event name, but VTEC says EH (old) or XH (new) warning
    for code in ("EH", "XH"):
        a = alert("Some New Heat Name", vtec=[f"/O.NEW.KJAX.{code}.W.0001.260720T1600Z-260721T0000Z/"])
        assert nws.classify(a) == ("heat", "danger")


def test_alert_matches_site_by_zone_or_polygon():
    zones = {"forecast": "FLZ136", "county": "FLC001", "fire": "FLZ136"}
    assert nws.alert_hits_site(alert("Flood Warning", ugc=["FLC001"]), GNV, zones)
    assert not nws.alert_hits_site(alert("Flood Warning", ugc=["FLC999"]), GNV, zones)
    box = {"type": "Polygon", "coordinates": [[[-82.5, 29.6], [-82.2, 29.6], [-82.2, 29.9],
                                               [-82.5, 29.9], [-82.5, 29.6]]]}
    assert nws.alert_hits_site(alert("Tornado Warning", geometry=box), GNV, {})


def test_alert_events_from_patched_feed_and_stale_fallback(monkeypatch):
    ctx = {GNV.site_id: {"zones": {"forecast": "FLZ136", "county": "FLC001"}}}
    feed = [alert("Extreme Heat Warning", ugc=["FLZ136"]),
            alert("Flood Warning", ugc=["FLZ136"], ends_h=-1)]          # already expired
    monkeypatch.setattr(nws, "fetch_fl_alerts", lambda: feed)
    events, result = nws.alert_events([GNV], ctx, now=T0)
    assert [(e.hazard, e.severity, e.pattern) for e in events] == [("heat", "danger", "HEAT_YELLOW")]

    # WiFi drops: same events, now marked stale
    def offline():
        raise OSError("network down")
    monkeypatch.setattr(nws, "fetch_fl_alerts", offline)
    events, result = nws.alert_events([GNV], ctx, now=T0 + timedelta(minutes=10))
    assert result.stale and events and all(e.stale for e in events)


def test_fake_mode_gives_alerts_and_heatrisk_without_internet():
    store.set_mode("fake")
    sites = list(load_sites())
    events, _ = nws.alert_events(sites, {}, now=T0)
    kinds = {(e.site_id, e.hazard) for e in events}
    assert ("eia-136", "flood") in kinds and ("eia-645", "heat") in kinds
    assert all(e.fake for e in events)
    risk, _ = nws.heatrisk_by_site(sites, now=T0)
    assert risk["eia-645"]["today"]["label"] == "Major"
