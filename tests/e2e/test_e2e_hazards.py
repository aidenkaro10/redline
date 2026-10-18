"""
6. Site hazards, end to end: session C1's lightning rule -> A's bridge -> bands and dashboard.

The rule (backend/config.py, never learned): 2+ good flashes within 10 miles -> STOP WORK, and
the all-clear comes 30 minutes after the LAST flash within 10 miles (every new flash resets it).

We use C1's fake mode (no internet) and drive its clock by hand: every hazard picture is built
with an explicit `now`, and C1's own 60 s background rebuild is switched off so nothing races us.
Workers at the crew site (eia-663, Deerhaven) must get BUZZ:LIGHTNING; a worker at another
site must not.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.hazards import contract, lightning, picture
from backend.hazards.geo import distance_mi
from backend.sites.sites import get_site, load_sites

CREW_SITE = "eia-663"
FAKE_MODE_SITES = {"eia-136", "eia-645", "eia-628", "eia-641", "eia-564"}     # C1's built-in demo hazards
T0 = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)


def far_quiet_site() -> str:
    """A site with no fake-mode hazard, well outside the crew site's 30-mile watch ring."""
    crew = get_site(CREW_SITE)
    return sorted(s.site_id for s in load_sites() if s.site_id not in FAKE_MODE_SITES | {CREW_SITE}
                  and distance_mi(crew.lat, crew.lon, s.lat, s.lon) > 80)[0]


@pytest.fixture
def site_app(live_app, monkeypatch):
    monkeypatch.setattr(picture, "start_background_refresh", lambda *a, **k: None)   # we build pictures ourselves
    app = live_app("--hazards", "fake")
    other = far_quiet_site()
    for wid, site in (("w-a", CREW_SITE), ("w-b", CREW_SITE), ("w-far", other)):
        assert app.client.post("/api/workers", json={"worker_id": wid, "name": wid, "site_id": site}).status_code == 200
    app.other_site = other
    return app


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def rebuild(app, minutes: float) -> dict:
    """C1 rebuilds its picture at T0 + minutes, then A's bridge does its 15 s refresh."""
    pic = picture.hazard_picture(now=at(minutes))
    app.on_loop(app.app.state.sites.refresh)
    return next(e for e in pic["sites"] if e["site"]["site_id"] == CREW_SITE)


def crew_lightning(app) -> dict:
    """The lightning row of the crew site's card, as the dashboard gets it."""
    hz = app.frontend.site_status(CREW_SITE)["hazards"][1]
    assert hz["kind"] == "lightning"
    return hz


def unix(minutes: float) -> float:
    return at(minutes).timestamp()


def test_A22_lightning_stop_work_buzzes_only_that_sites_bands(site_app):
    app = site_app
    rebuild(app, 0)
    assert app.hub.pipelines["w-a"].router.current == "OFF"

    contract.test_alert("LIGHTNING", CREW_SITE, now=at(0))      # 2 flashes, 3 miles away
    entry = rebuild(app, 0.5)
    assert entry["lightning"]["state"] == "stop_work"
    assert entry["band_pattern"] == "LIGHTNING"

    # Bands at the crew site get BUZZ:LIGHTNING, the far site's band doesn't.
    assert app.band.lines_for("w-a")[-1:] == ["BUZZ:LIGHTNING"]
    assert app.band.lines_for("w-b")[-1:] == ["BUZZ:LIGHTNING"]
    assert "BUZZ:LIGHTNING" not in app.band.lines_for("w-far")
    # The crew lead's card: stop work, the strike ring, a countdown to T0 + 30 min.
    hz = crew_lightning(app)
    assert hz["stop_work"] is True and hz["level"] == "extreme" and hz["fake"] is True
    assert hz["all_clear_at"] == pytest.approx(unix(config.LIGHTNING_ALL_CLEAR_MIN))
    assert hz["strikes"] and all(s["miles"] == pytest.approx(3.0, abs=0.2) for s in hz["strikes"])
    status = app.client.get("/api/workers").json()
    assert {w["worker_id"]: w["hazard"] for w in status} == {"w-a": "LIGHTNING", "w-b": "LIGHTNING", "w-far": None}
    # Every hazard change is logged with its source and time (track brief: reporting).
    hazard_events = [e for e in app.of_type("hazard") if e["site_id"] == CREW_SITE]
    assert hazard_events[-1]["on"] and hazard_events[-1]["source"] == "C1 hazard feeds"


def test_A22_all_clear_countdown_resets_on_a_new_strike(site_app):
    app = site_app
    contract.test_alert("LIGHTNING", CREW_SITE, now=at(0))
    rebuild(app, 0.5)
    assert crew_lightning(app)["all_clear_at"] == pytest.approx(unix(30))

    entry = rebuild(app, 10)                                     # quiet for 10 min: countdown runs
    assert entry["lightning"]["minutes_to_all_clear"] == pytest.approx(20.0, abs=0.1)

    lightning.inject_strikes(get_site(CREW_SITE), distance_mi=6.0, n=1, now=at(20))   # a new strike, 6 mi
    entry = rebuild(app, 20.5)
    assert entry["lightning"]["state"] == "stop_work"
    assert crew_lightning(app)["all_clear_at"] == pytest.approx(unix(50))        # reset: 20 + 30 min
    assert entry["lightning"]["minutes_to_all_clear"] == pytest.approx(29.5, abs=0.1)

    rebuild(app, 49)                                             # 29 quiet minutes is not enough
    assert app.hub.pipelines["w-a"].router.current == "LIGHTNING"
    assert crew_lightning(app)["stop_work"] is True

    entry = rebuild(app, 50.5)                                   # 30 quiet minutes: all clear
    assert entry["lightning"]["state"] != "stop_work"
    assert app.band.lines_for("w-a")[-1] == "BUZZ:OFF"
    assert app.band.lines_for("w-b")[-1] == "BUZZ:OFF"
    assert crew_lightning(app)["stop_work"] is False


def test_A22_one_flash_or_a_far_storm_does_not_stop_work(site_app):
    """The safety rule's edges: 1 flash waits for a 2nd, flashes past 10 miles only show 'nearby'."""
    app = site_app
    site = get_site(CREW_SITE)
    lightning.inject_strikes(site, distance_mi=4.0, n=1, now=at(0))
    entry = rebuild(app, 0.2)
    assert entry["lightning"]["state"] == "unconfirmed"
    assert app.hub.pipelines["w-a"].router.current == "OFF"

    lightning.clear_injected_strikes()
    lightning.inject_strikes(site, distance_mi=12.0, n=3, now=at(5))
    entry = rebuild(app, 5.2)
    assert entry["lightning"]["state"] == "nearby"
    assert app.hub.pipelines["w-a"].router.current == "OFF"
    assert "BUZZ:LIGHTNING" not in app.band.lines_for("w-a")


def test_A23_flood_warning_buzzes_flood_fire(site_app):
    app = site_app
    contract.test_alert("FLOOD_FIRE", CREW_SITE, now=at(0))
    rebuild(app, 1)
    assert app.band.lines_for("w-a")[-1:] == ["BUZZ:FLOOD_FIRE"]
    assert "BUZZ:FLOOD_FIRE" not in app.band.lines_for("w-far")
    contract.clear_test_hazards()
    rebuild(app, 2)
    assert app.band.lines_for("w-a")[-1] == "BUZZ:OFF"


def test_demo_test_alert_shows_on_the_site_card_right_away(live_app):
    app = live_app("--hazards", "fake")
    app.on_loop(app.app.state.sites.refresh)
    assert app.client.post("/api/workers", json={"worker_id": "w-a", "site_id": CREW_SITE}).status_code == 200
    import time
    deadline = time.monotonic() + 20
    while picture.latest_picture() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    app.client.post("/api/demo/test-alert", json={"kind": "LIGHTNING"})
    assert app.band.lines_for("w-a")[-1:] == ["BUZZ:LIGHTNING"]          # this part works
    app.on_loop(app.app.state.sites.refresh)                              # the bridge's next poll
    assert crew_lightning(app)["stop_work"] is True
