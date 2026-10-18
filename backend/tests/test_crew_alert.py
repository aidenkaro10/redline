"""Crewmate alert bookkeeping in the hub: overlapping incidents, removal."""

from backend.alerts.band_output import MultiBand
from backend.app.hub import Hub


def make():
    hub = Hub(band=MultiBand([]))
    for w in ("a", "b", "c"):
        hub.register_worker(w, name=w.upper())
    return hub


def test_overlapping_incidents_keep_a_crewmate_alerted_until_the_last_ends():
    hub = make()
    hub.dispatch(hub._crew_alert_on("a", ["b", "c"], 1.0))
    hub.dispatch(hub._crew_alert_on("b", ["a", "c"], 2.0))
    assert hub.pipelines["c"].router.current == "MANDOWN"
    assert hub._crew_alert_on("a", ["b", "c"], 3.0) == []          # same incident: alerted once
    hub.dispatch(hub.end_crew_alert("a"))
    assert hub.pipelines["c"].router.current == "MANDOWN"          # still needed for b's incident
    assert hub.pipelines["b"].router.current == "OFF"              # b's own alert from a is over
    hub.dispatch(hub.end_crew_alert("b"))
    assert hub.pipelines["c"].router.current == "OFF"
    assert hub.end_crew_alert("b") == []


def test_removing_the_downed_worker_clears_their_crewmates():
    hub = make()
    hub.dispatch(hub._crew_alert_on("a", ["b", "c"], 1.0))
    hub.remove_worker("a")
    assert hub.pipelines["b"].router.current == "OFF" and hub.crew_alerts == {}
