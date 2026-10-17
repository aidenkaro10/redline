"""Tests for the ranked hazard picture (all feeds together). Fake mode: no internet."""

import json
from datetime import timedelta

from backend.hazards import picture, store
from backend.hazards.events import parse_time
from backend.hazards.inject import clear_injected, inject_test_event
from backend.hazards.report_log import get_log
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)

# Minute 10 of the hour: the fake storm is over Crystal River
NOW = parse_time("2026-07-20T18:10:00Z")


def test_fake_picture_covers_all_sites_ranked_and_json_ready():
    store.set_mode("fake")
    pic = picture.hazard_picture(now=NOW)
    assert pic["site_count"] >= 50 and len(pic["sites"]) == pic["site_count"]
    scores = [e["score"] for e in pic["sites"]]
    assert scores == sorted(scores, reverse=True)
    assert [e["rank"] for e in pic["sites"]] == list(range(1, len(scores) + 1))
    # Lightning stop-work is the worst thing happening, so it ranks first
    top = pic["sites"][0]
    assert top["band_pattern"] == "LIGHTNING" and top["top_severity"] == "danger"
    assert all(not f["stale"] for f in pic["feeds"].values())
    json.dumps(pic)                                           # serializable as-is


def test_every_site_entry_has_the_fields_the_map_and_briefing_use():
    store.set_mode("fake")
    pic = picture.hazard_picture(now=NOW)
    for e in pic["sites"]:
        assert {"site", "score", "events", "weather", "heat_risk", "lightning", "flood",
                "static", "tomorrow", "band_pattern", "rank", "hazard_index"} <= set(e)
        assert e["weather"]["wbgt_c"] is not None


def test_band_commands_and_report_log():
    store.set_mode("fake")
    pic = picture.hazard_picture(now=NOW)
    cmds = picture.band_commands(pic)
    assert cmds["eia-628"] == "BUZZ:LIGHTNING"
    assert cmds["eia-136"] == "BUZZ:FLOOD_FIRE"
    kinds = {r["kind"] for r in get_log().read()}
    assert "reported" in kinds
    # Next refresh with nothing new: no duplicate "reported" records
    n = len(get_log().read())
    picture.hazard_picture(now=NOW + timedelta(seconds=30))
    assert all(r["kind"] != "reported" for r in get_log().read()[n:])


def test_injected_event_jumps_to_the_top():
    store.set_mode("fake")
    inject_test_event("eia-663", "flood", severity="danger", now=NOW)
    pic = picture.hazard_picture(now=NOW)
    gnv = picture.site_entry("eia-663", pic)
    assert gnv["top_severity"] == "danger" and gnv["band_pattern"] == "FLOOD_FIRE"
    assert gnv["rank"] <= 10
    clear_injected()


def test_one_broken_feed_does_not_kill_the_picture(monkeypatch):
    store.set_mode("fake")
    from backend.hazards import wildlife

    def broken(*a, **k):
        raise ValueError("unexpected API answer")
    monkeypatch.setattr(wildlife, "wildlife_events", broken)
    pic = picture.hazard_picture(now=NOW)
    assert pic["feeds"]["wildlife"]["stale"] and "crashed" in pic["feeds"]["wildlife"]["error"]
    assert pic["sites"][0]["band_pattern"] == "LIGHTNING"
