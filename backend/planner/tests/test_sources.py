"""Tests for personal offsets and the C1/C2 adapters (fake data shaped like their real output)."""

import datetime as dt
import os

import numpy as np
import pytest

os.environ["PLANNER_LLM"] = "off"

from backend import config
from backend.planner import briefing as B
from backend.planner.generate import demo_scenario
from backend.planner.heat_model import PHSHeatModel
from backend.planner.personal import apply_offsets, planner_offset
from backend.planner.scenario import REST, Plan
from backend.planner.sources import TZ, hazard_windows_from_events, slot_weather_from_hourly

DATE = "2026-10-18"


def fake_hourly(date=DATE, hot=0.0):
    """C1 site_weather()[id]["hourly"] shape: UTC ISO times + Open-Meteo columns + Liljegren wbgt."""
    start = dt.datetime.fromisoformat(date).replace(tzinfo=TZ) - dt.timedelta(hours=12)
    times = [start + dt.timedelta(hours=h) for h in range(48)]
    local = np.array([(t.astimezone(TZ).hour) for t in times])
    day = np.cos((local - 15) / 24 * 2 * np.pi)
    return {"time": [t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ") for t in times],
            "temperature_2m": list(27 + hot + 5 * day), "relative_humidity_2m": list(75 - 15 * day),
            "wind_speed_10m": [3.0] * 48, "shortwave_radiation": list(np.clip(900 * day, 0, None)),
            "wbgt": list(25 + hot + 5 * day)}


def test_personal_offset_sign_and_guardrails():
    # model reads them 0.4 hotter than the thermometer -> they run COOLER -> capped at the cooler limit
    off, sd = planner_offset({"offset_c": 0.4, "confidence": 0.6})
    assert off == -config.PLAN_MAX_COOLER_OFFSET_C
    assert config.PLAN_MIN_PERSON_SD_C <= sd < config.PLAN_PERSON_SD_C
    # model reads them 0.5 COLDER -> they run hotter -> no cap on that side
    assert planner_offset({"offset_c": -0.5, "confidence": 0.9})[0] == 0.5
    assert planner_offset({"offset_c": 0.0, "confidence": 1.0})[1] == config.PLAN_MIN_PERSON_SD_C


def test_hotter_runner_gets_hotter_plan_forecast():
    sc = demo_scenario()
    wid = sc.crew[0].id
    plan = Plan({w.id: [REST] * sc.n_slots for w in sc.crew})
    base = PHSHeatModel().simulate(sc, [plan])[0][wid]
    assert apply_offsets(sc, {wid: {"offset_c": -0.3, "confidence": 0.8}}) == [wid]
    hot = PHSHeatModel().simulate(sc, [plan])[0][wid]
    assert hot[:, 1].max() == pytest.approx(base[:, 1].max() + 0.3, abs=0.05)     # median shifts by the offset
    assert hot[:, 2].max() > base[:, 2].max() + 0.1      # p90 rises less: more checks = less uncertainty


def test_slot_weather_from_c1_hourly():
    wx = slot_weather_from_hourly(fake_hourly(), DATE, "07:00", 16)
    assert len(wx) == 16
    assert wx[0].tdb < wx[-1].tdb                       # morning cooler than afternoon
    assert all(w.solar >= 0 and w.wind > 0 for w in wx)
    assert slot_weather_from_hourly(fake_hourly(), "2027-01-01", "07:00", 16) is None   # not covered


def test_hazard_windows_from_c1_events():
    t = lambda h: dt.datetime.fromisoformat(DATE).replace(hour=h, tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()
    events = [
        {"hazard": "weather", "title": "Severe Thunderstorm Warning", "source": "NWS", "starts": t(13), "ends": t(14)},
        {"hazard": "heat", "title": "Heat Advisory", "source": "NWS", "starts": t(10), "ends": t(19)},
        {"hazard": "lightning", "title": "Lightning within 10 mi", "source": "GOES-19 GLM", "starts": t(8),
         "ends": t(9), "details": {"state": "stop_work"}},
        {"hazard": "wildlife", "title": "Alligator sighting", "source": "iNaturalist", "starts": t(9), "ends": None},
        {"hazard": "weather", "title": "Tornado Warning", "source": "NWS", "starts": t(20), "ends": t(21)},  # after shift
    ]
    w = {h.kind: (h.start_slot, h.end_slot) for h in hazard_windows_from_events(events, DATE, "07:00", 16)}
    assert w == {"severe_thunderstorm": (12, 14), "heat_advisory": (6, 16), "lightning": (2, 4)}


def test_briefing_uses_c1_c2_when_available(monkeypatch):
    t = lambda h: dt.datetime.fromisoformat(DATE).replace(hour=h, tzinfo=TZ).astimezone(dt.timezone.utc).isoformat()
    picture = {"sites": [
        {"site": {"site_id": "eia-628", "name": "Big Bend", "lat": 27.8, "lon": -82.4},
         "events": [{"hazard": "weather", "title": "Severe Thunderstorm Warning", "level": 3, "source": "NWS",
                     "starts": t(13), "ends": t(15)}], "tomorrow": {"heat_risk": {"value": 3, "label": "Major"}}},
        {"site": {"site_id": "eia-663", "name": "Deerhaven Generating Station", "lat": 29.76, "lon": -82.39},
         "events": [], "tomorrow": {}}]}
    monkeypatch.setattr(B, "c1_picture", lambda: picture)
    monkeypatch.setattr(B, "c1_site", lambda sid: None)
    monkeypatch.setattr(B, "c1_hourly", lambda sid: fake_hourly(hot=2.0 if sid == "eia-628" else 0.0))
    monkeypatch.setattr(B, "c2_index", lambda sid, date: {"index": 7.2 if sid == "eia-628" else 3.1, "band": "extreme",
                                                          "top_reasons": [{"hazard": "storm", "text": "SPC slight risk"}]})
    monkeypatch.setattr(B, "c2_incidents", lambda sid: [{"date": "2023-08-01", "text": "Heat illness", "source": "OSHA Severe Injury Reports"}])
    b = B.make_briefing(for_date=DATE, use_llm=False, offsets={"w1": {"offset_c": -0.2, "confidence": 0.7}})
    assert [s["site_id"] for s in b["sites"]] == ["eia-628", "eia-663"]
    assert all(s["data_sources"] == {"index": "C2", "hazards": "C1", "weather": "C1", "incidents": "C2"} for s in b["sites"])
    assert b["stubs"] == []
    big_bend = b["sites"][0]
    assert big_bend["top_hazards"][0]["text"] == "Severe Thunderstorm Warning"
    assert any("Severe thunderstorm" in w["text"] for w in big_bend["windows"])
    assert b["schedule"]["checked_by_rules"] and b["personalized_workers"] == ["w1"]
    assert any("spot checks" in x for x in b["schedule_suggestions"])


def test_briefing_offline_is_all_stubs():
    b = B.make_briefing(for_date=DATE, use_llm=False, live=False, offsets={})
    assert b["stubs"] and all(set(s["data_sources"].values()) == {"stub"} for s in b["sites"])
    assert b["schedule"]["checked_by_rules"]
