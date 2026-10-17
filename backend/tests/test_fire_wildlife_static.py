"""Tests for wildfire, wildlife and static flood context. No internet."""

from backend.hazards import store, wildfire, wildlife
from backend.hazards.events import parse_time
from backend.hazards.static_context import fema_risk, static_flood_summary
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site

NOW = parse_time("2026-10-17T15:00:00Z")
SITE = get_site("eia-663")       # Deerhaven, Gainesville

FIRMS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
29.7900,-82.3878,330.1,0.4,0.4,2026-10-17,0712,N20,VIIRS,h,2.0NRT,290.2,12.5,N
29.9500,-82.3878,310.0,0.4,0.4,2026-10-17,0712,N20,VIIRS,n,2.0NRT,288.0,3.1,N
"""


def test_firms_csv_and_detection_severity(monkeypatch):
    dets = wildfire.parse_firms_csv(FIRMS_CSV, "VIIRS_NOAA20_NRT")
    assert dets[0]["time"] == "2026-10-17T07:12:00Z" and dets[0]["confidence"] == "h"
    monkeypatch.setenv("FIRMS_MAP_KEY", "test-key")
    monkeypatch.setattr(wildfire, "fetch_firms", lambda: dets)
    monkeypatch.setattr(wildfire, "fetch_nifc", lambda: [])
    events, _ = wildfire.wildfire_events([SITE], now=NOW)
    # The closest detection (~2 mi, high confidence) wins: warning, FLOOD_FIRE pattern
    assert len(events) == 1 and events[0].severity == "warning" and events[0].pattern == "FLOOD_FIRE"


def test_no_firms_key_is_reported_not_crashed(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.setattr(wildfire, "fetch_nifc", lambda: [])
    events, feeds = wildfire.wildfire_events([SITE], now=NOW)
    assert events == [] and feeds["firms"].stale and "FIRMS_MAP_KEY" in feeds["firms"].error


def test_nifc_wildfire_vs_prescribed_burn(monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    incidents = [
        {"id": "A", "name": "OAK", "lat": 29.80, "lon": -82.39, "acres": 50, "contained_pct": 10, "type": "WF"},
        {"id": "B", "name": "RX 1", "lat": 29.78, "lon": -82.39, "acres": 20, "contained_pct": None, "type": "RX"},
        {"id": "C", "name": "FAR", "lat": 27.0, "lon": -81.0, "acres": 900, "contained_pct": 0, "type": "WF"},
    ]
    monkeypatch.setattr(wildfire, "fetch_nifc", lambda: incidents)
    events, _ = wildfire.wildfire_events([SITE], now=NOW)
    assert {(e.source_id, e.severity) for e in events} == {("A", "warning"), ("B", "info")}


def test_wildlife_groups_and_close_alligator(monkeypatch):
    def obs(i, lat, group):
        return {"id": i, "group": group, "lat": lat, "lon": SITE.lon, "observed_on": "2026-10-16",
                "name": group, "observer": "x", "license": "cc-by-nc", "url": "u", "obscured": False}
    data = [obs(1, SITE.lat + 0.5 / 69, "alligator"), obs(2, SITE.lat + 3 / 69, "alligator"),
            obs(3, SITE.lat + 2 / 69, "fire ants"), obs(4, SITE.lat + 20 / 69, "pit viper")]
    monkeypatch.setattr(wildlife, "fetch_sightings", lambda now=None: data)
    events, _ = wildlife.wildlife_events([SITE], now=NOW)
    by_group = {e.details["group"]: e for e in events}
    assert by_group["alligator"].severity == "advisory" and by_group["alligator"].details["count"] == 2
    assert by_group["fire ants"].severity == "info"
    assert "pit viper" not in by_group                      # 20 miles away


def test_inat_group_from_ancestors():
    assert wildlife.group_of({"taxon": {"id": 30668, "ancestor_ids": [1, 797576, 30668]}}) == "pit viper"
    assert wildlife.group_of({"taxon": {"id": 26159, "ancestor_ids": [1]}}) == "alligator"


def test_fema_and_levee_summary():
    assert fema_risk("VE")[0] == 3 and fema_risk("AE")[0] == 2
    assert fema_risk("X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD")[0] == 1 and fema_risk("X")[0] == 0
    ctx = {"fema": {"zone": "X", "risk": 0, "label": "minimal flood hazard"},
           "levees": {"system": "L-14", "inside_leveed_area": True, "accredited": False}}
    s = static_flood_summary(ctx)
    assert s["static_flood_risk"] == 1 and "non-accredited levee L-14" in s["summary"]


def test_fake_fire_and_wildlife():
    store.set_mode("fake")
    from backend.sites.sites import load_sites
    sites = list(load_sites())
    assert any(e.site_id == "eia-641" for e in wildfire.wildfire_events(sites, now=NOW)[0])
    assert any(e.site_id == "eia-564" for e in wildlife.wildlife_events(sites, now=NOW)[0])
