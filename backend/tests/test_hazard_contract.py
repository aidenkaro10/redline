"""Tests for the frontend-contract adapter (Site, SiteStatus, demo test alerts)."""

from datetime import timedelta

from backend.hazards import contract, picture, store
from backend.hazards.events import parse_time
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.hazards.weather import heat_index_f

NOW = parse_time("2026-07-20T18:10:00Z")


def test_site_records():
    sites = contract.site_records()
    assert len(sites) >= 50
    assert {"id", "name", "operator", "tech", "capacity_mw", "lat", "lon", "county", "coastal"} <= set(sites[0])
    assert any(s["coastal"] for s in sites)


def test_site_status_shape_six_kinds_in_order():
    store.set_mode("fake")
    statuses = contract.site_statuses(picture.hazard_picture(now=NOW))
    st = {s["site_id"]: s for s in statuses}
    cr = st["eia-628"]                                   # fake storm at Crystal River
    assert [h["kind"] for h in cr["hazards"]] == list(contract.KINDS)
    lt = cr["hazards"][1]
    assert lt["stop_work"] and lt["level"] == "extreme" and lt["band_pattern"] == "LIGHTNING"
    assert lt["all_clear_at"] > NOW.timestamp() and lt["strikes"] and lt["strikes"][0]["miles"] <= 30
    heat = st["eia-645"]["hazards"][0]
    assert heat["wbgt_c"] and heat["heat_index_f"] > 90 and heat["level"] in ("high", "extreme")
    assert st["eia-663"]["hazards"][4]["level"] == "none"   # no soil problem at Deerhaven


def test_test_alert_lightning_at_default_site_then_clear():
    store.set_mode("fake")
    contract.test_alert("LIGHTNING", now=NOW)
    pic = picture.hazard_picture(now=NOW + timedelta(seconds=5))
    gnv = [s for s in contract.site_statuses(pic) if s["site_id"] == "eia-663"][0]
    assert gnv["band_pattern"] == "LIGHTNING" and gnv["hazards"][1]["stop_work"]
    contract.clear_test_hazards()
    pic = picture.hazard_picture(now=NOW + timedelta(seconds=10))
    gnv = [s for s in contract.site_statuses(pic) if s["site_id"] == "eia-663"][0]
    assert gnv["band_pattern"] is None


def test_heat_index_matches_nws_table():
    # NWS heat index chart: 90 °F at 70% RH -> 106 °F; 100 °F at 40% -> 109 °F
    assert abs(heat_index_f((90 - 32) * 5 / 9, 70) - 106) <= 1
    assert abs(heat_index_f((100 - 32) * 5 / 9, 40) - 109) <= 1
