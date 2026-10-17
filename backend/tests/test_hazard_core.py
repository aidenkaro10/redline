"""Tests for the hazard event model, the feed cache and the report log."""

from datetime import timedelta

import pytest

from backend.hazards.cache import FeedCache
from backend.hazards.events import HazardEvent, band_pattern, parse_time
from backend.hazards.inject import inject_test_event, injected_events
from backend.hazards.report_log import HazardLog, get_log
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)

T0 = parse_time("2026-10-17T18:00:00Z")


def ev(**kw):
    base = dict(site_id="eia-663", hazard="flood", severity="warning",
                title="Flood Warning", source="NWS", source_id="abc")
    base.update(kw)
    return HazardEvent(**base)


def test_band_patterns_follow_spec():
    assert band_pattern("lightning", "danger") == "LIGHTNING"
    assert band_pattern("flood", "warning") == "FLOOD_FIRE"
    assert band_pattern("fire", "warning") == "FLOOD_FIRE"
    assert band_pattern("flood", "advisory") is None
    assert band_pattern("wildlife", "danger") is None


def test_event_validates_and_serializes():
    d = ev().to_dict()
    assert d["event_id"] == "eia-663|flood|NWS|abc"
    assert d["band_pattern"] == "FLOOD_FIRE" and d["level"] == 3
    with pytest.raises(ValueError):
        ev(hazard="zombies")


def test_cache_serves_last_good_copy_when_wifi_drops():
    cache = FeedCache("test_feed", max_age_s=60)
    r1 = cache.get(lambda: {"temp": 31}, now=T0)
    assert r1.data == {"temp": 31} and not r1.stale

    # Within max age: no call at all
    r2 = cache.get(lambda: 1 / 0, now=T0 + timedelta(seconds=30))
    assert r2.data == {"temp": 31} and not r2.stale

    # Expired and the network is down: last good copy, marked stale
    def offline():
        raise OSError("no route to host")
    r3 = cache.get(offline, now=T0 + timedelta(minutes=5))
    assert r3.data == {"temp": 31} and r3.stale and "no route" in r3.error


def test_cache_survives_restart():
    FeedCache("persist", max_age_s=60).get(lambda: [1, 2, 3], now=T0)
    fresh_process = FeedCache("persist", max_age_s=60)
    r = fresh_process.get(lambda: (_ for _ in ()).throw(OSError("offline")),
                          now=T0 + timedelta(hours=1))
    assert r.data == [1, 2, 3] and r.stale


def test_log_reports_updates_and_clears(tmp_path):
    log = HazardLog(tmp_path / "log.jsonl")
    assert [r["kind"] for r in log.sync([ev()], at=T0)] == ["reported"]
    assert log.sync([ev()], at=T0) == []                         # nothing new
    assert [r["kind"] for r in log.sync([ev(severity="danger")], at=T0)] == ["updated"]
    # Feed down: don't clear its events
    assert log.sync([], at=T0, skip_clear_sources={"NWS"}) == []
    assert [r["kind"] for r in log.sync([], at=T0)] == ["cleared"]
    # Append-only: three lines, and a restart remembers nothing is open
    assert len(log.read()) == 3
    assert HazardLog(tmp_path / "log.jsonl")._open == {}


def test_inject_test_event_shows_up_and_expires():
    d = inject_test_event("eia-663", "flood", minutes=10, now=T0)
    assert d["fake"] and d["band_pattern"] == "FLOOD_FIRE"
    assert len(injected_events(now=T0 + timedelta(minutes=5))) == 1
    assert injected_events(now=T0 + timedelta(minutes=11)) == []
    assert get_log().read()[-1]["kind"] == "injected"
