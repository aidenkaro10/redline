"""
Storm chances per 30-min slot, for probability-aware scheduling.

Sources:
  Open-Meteo hourly forecast (free, no key): precipitation_probability (%) and weather_code
      (95-99 = thunderstorm). storm chance = rain chance when a thunderstorm code is forecast,
      else a quarter of it (Florida summer showers often bring lightning). A rough rule, said so.
  NWS (through C1's hazard events): a Severe Thunderstorm / Tornado WATCH over a slot raises
      its storm chance to at least 0.7. (Warnings are hard stop-work windows already.)
  PLANNER_STORM_MODE: live (default) | fake (60% storms 2-4 PM, for demos; also when C1's
      HAZARD_MODE=fake) | off (never fetch; keep whatever the scenario has; tests use this).

How it's used: a SOFT cost in the optimizer (optimizer.py, storm_penalty). Likely-storm slots get
expensive for outdoor work, more for elevated and hard-to-stop tasks, so that work moves earlier.
The real lightning rule (10 miles, 30-min all-clear) stays a HARD constraint in rules.py.

Every function fails soft: no network or a bad answer = storm chances stay 0 and the plan says so.
"""

import datetime as dt
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np

from backend.planner.scenario import Scenario

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/New_York")
URL = "https://api.open-meteo.com/v1/forecast"
CACHE_S = 1800
THUNDER_CODES = range(95, 100)
NON_THUNDER_FACTOR = 0.25
WATCH_MIN_PROB = 0.7
_cache: dict = {}


def mode() -> str:
    m = os.getenv("PLANNER_STORM_MODE", "live").lower()
    return "fake" if fake_mode() else ("off" if m == "off" else "live")


def fake_mode() -> bool:
    return os.getenv("PLANNER_STORM_MODE", "").lower() == "fake" or os.getenv("HAZARD_MODE", "").lower() == "fake"


def fetch_hourly(lat: float, lon: float) -> dict | None:
    """Open-Meteo hourly {time: [unix s], storm: [0..1], rain: [0..1]}, cached 30 min. None on any failure."""
    key = (round(lat, 3), round(lon, 3))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    q = urllib.parse.urlencode({"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
                                "hourly": "precipitation_probability,weather_code",
                                "timezone": "GMT", "forecast_days": 3, "past_days": 1})
    try:
        with urllib.request.urlopen(f"{URL}?{q}", timeout=10) as r:
            h = json.loads(r.read())["hourly"]
        times = [dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc).timestamp() for t in h["time"]]
        rain = np.array([0 if v is None else v for v in h["precipitation_probability"]], float) / 100
        codes = [0 if c is None else c for c in h["weather_code"]]
        storm = np.where([c in THUNDER_CODES for c in codes], rain, rain * NON_THUNDER_FACTOR)
        out = {"time": times, "storm": storm.tolist(), "rain": rain.tolist()}
    except Exception as e:
        log.info("Open-Meteo storm chances unavailable: %s", e)
        return None
    _cache[key] = (time.time(), out)
    return out


def slot_mids(scenario: Scenario) -> np.ndarray:
    h, m = map(int, scenario.shift_start.split(":"))
    d = dt.date.fromisoformat(scenario.date)
    start = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp()
    return start + (np.arange(scenario.n_slots) + 0.5) * scenario.slot_min * 60


def fake_probs(scenario: Scenario) -> tuple[list[float], list[float]]:
    """Demo: 60% storms 2-4 PM, 30% the hour before."""
    storm = []
    for k in range(scenario.n_slots):
        h, m = map(int, scenario.slot_time(k).split(":"))
        hour = h + m / 60
        storm.append(0.6 if 14 <= hour < 16 else 0.3 if 13 <= hour < 14 else 0.0)
    return storm, storm


def watch_slots(scenario: Scenario, events: list[dict] | None) -> set[int]:
    """Slots covered by an NWS Severe Thunderstorm / Tornado WATCH (C1 HazardEvents)."""
    out = set()
    mids = slot_mids(scenario)
    for e in events or []:
        title = (e.get("title") or "").lower()
        if "watch" not in title or not ("thunderstorm" in title or "tornado" in title):
            continue
        try:
            a = dt.datetime.fromisoformat(e["starts"].replace("Z", "+00:00")).timestamp() if e.get("starts") else -np.inf
            b = dt.datetime.fromisoformat(e["ends"].replace("Z", "+00:00")).timestamp() if e.get("ends") else np.inf
        except (TypeError, ValueError):
            continue
        out |= {k for k, t in enumerate(mids) if a <= t < b}
    return out


def add_storm_probs(scenario: Scenario, events: list[dict] | None = None, live: bool = True) -> str:
    """
    Fill weather[k].storm_prob / rain_prob for the scenario's site and day (in place).
    Returns where the numbers came from: "fake", "open-meteo", "open-meteo+nws", "nws" or "none".
    Existing non-zero chances (e.g. from a generated scenario) are kept if nothing better is available.
    """
    if fake_mode():
        storm, rain = fake_probs(scenario)
        source = "fake"
    else:
        live = live and mode() == "live"
        hourly = fetch_hourly(scenario.site["lat"], scenario.site["lon"]) if live and scenario.site.get("lat") else None
        if hourly:
            mids = slot_mids(scenario)
            t = np.array(hourly["time"]) - 1800          # each hourly value covers the hour around it
            storm = np.interp(mids, t, hourly["storm"]).tolist()
            rain = np.interp(mids, t, hourly["rain"]).tolist()
            source = "open-meteo"
        else:
            storm = [w.storm_prob for w in scenario.weather]
            rain = [w.rain_prob for w in scenario.weather]
            source = "none"
    watched = watch_slots(scenario, events)
    for k in watched:
        storm[k] = max(storm[k], WATCH_MIN_PROB)
    if watched:
        source = "nws" if source == "none" else source + "+nws"
    for w, s, r in zip(scenario.weather, storm, rain):
        w.storm_prob, w.rain_prob = round(float(s), 2), round(float(max(r, s)), 2)
    return source


def storm_windows(scenario: Scenario, threshold: float = 0.4) -> list[tuple[int, int, float]]:
    """Runs of slots with storm chance >= threshold: (start_slot, end_slot exclusive, peak chance)."""
    out, k = [], 0
    probs = [w.storm_prob for w in scenario.weather]
    while k < len(probs):
        if probs[k] >= threshold:
            j = k
            while j < len(probs) and probs[j] >= threshold:
                j += 1
            out.append((k, j, max(probs[k:j])))
            k = j
        else:
            k += 1
    return out


def storm_reason(scenario: Scenario, plan) -> str | None:
    """
    Plain words for the plan, e.g. "60% storms 1:30-3 PM: Scaffold setup moved to 8-9:30 AM."
    Lists where storm-sensitive outdoor tasks (elevated, hard to stop, heavy) landed.
    """
    wins = storm_windows(scenario)
    if not wins:
        return None
    clock = lambda k: _clock(scenario.slot_time(k))
    when = ", ".join(f"{p:.0%} storms {clock(a)}-{clock(b)}" for a, b, p in wins)
    parts = []
    for t in scenario.tasks:
        if not t.outdoor or not (t.elevated or t.hard_to_stop or t.intensity in ("heavy", "very_heavy")):
            continue
        slots = sorted({k for row in plan.assignments.values() for k, a in enumerate(row) if a == t.id})
        if slots:
            parts.append(f"{t.name} at {clock(slots[0])}-{clock(slots[-1] + 1)}")
    return f"{when}: " + ("; ".join(parts) if parts else "outdoor work kept out of it") + "."


def _clock(hhmm: str) -> str:
    """'13:30' -> '1:30 PM', '09:00' -> '9 AM'."""
    h, m = map(int, hhmm.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = (h - 1) % 12 + 1
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"
