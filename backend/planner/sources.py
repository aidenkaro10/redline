"""
Adapters to the other sessions' data, each with a clean fallback.

    C1 (backend/hazards, backend/sites)  sites, hourly forecast + Liljegren WBGT, NWS/lightning events
    C2 (ml/hazard_index)                 tomorrow's 0-10 hazard index, past incidents near a site

Every function returns None when the other session's code isn't merged, a feed is down,
or nothing is cached. The briefing then uses its stubs and says so. Nothing here can
raise into the planner.

Written against: C1 branch claude/site-hazards-72b44b (29119a1), C2 branch
claude/hazard-index-739442 (02a10fa). Interfaces: docs/interfaces/site-hazards.md,
docs/interfaces/hazard-index.md.
"""

import datetime as dt
import logging
from zoneinfo import ZoneInfo

import numpy as np

from backend.planner.scenario import HazardWindow, SlotWeather

log = logging.getLogger(__name__)
TZ = ZoneInfo("America/New_York")
WIND_10M_TO_BODY = 0.67    # 10 m wind -> roughly 2 m, near the body (log wind profile, open ground)

# NWS event title keywords -> planner hazard kinds (stop-work kinds are in config.PLAN_STOP_WORK_HAZARDS)
TITLE_KINDS = [("tornado", "tornado"), ("severe thunderstorm", "severe_thunderstorm"),
               ("flash flood", "flash_flood"), ("flood warning", "flood"), ("hurricane", "hurricane"),
               ("tropical storm", "tropical_storm"), ("extreme heat", "heat_advisory"),
               ("excessive heat", "heat_advisory"), ("heat advisory", "heat_advisory"),
               ("red flag warning", "red_flag_fire")]


# ---------------------------------------------------------------------------
# C1: sites, weather, hazard picture
# ---------------------------------------------------------------------------
def c1_site(site_id: str) -> dict | None:
    """{"id", "name", "lat", "lon"} from C1's plants.geojson, or None."""
    try:
        from backend.sites.sites import get_site
        s = get_site(site_id)
        return {"id": s.site_id, "name": s.name, "lat": s.lat, "lon": s.lon}
    except Exception:
        return None


def c1_picture() -> dict | None:
    """C1's ranked hazard picture (latest if the server built one, else build it now), or None."""
    try:
        from backend.hazards.picture import hazard_picture, latest_picture
        return latest_picture() or hazard_picture(write_log=False)
    except Exception as e:
        log.info("C1 hazard picture unavailable: %s", e)
        return None


def c1_entry(picture: dict | None, site_id: str) -> dict | None:
    if not picture:
        return None
    return next((e for e in picture.get("sites", []) if (e.get("site") or {}).get("site_id") == site_id), None)


def c1_hourly(site_id: str) -> dict | None:
    """C1's hourly forecast for one site (time, temperature_2m, ..., wbgt), or None."""
    try:
        from backend.hazards.weather import site_weather
        from backend.sites.sites import get_site
        wx, _feed = site_weather([get_site(site_id)])
        return (wx.get(site_id) or {}).get("hourly")
    except Exception as e:
        log.info("C1 weather unavailable for %s: %s", site_id, e)
        return None


def slot_weather_from_hourly(hourly: dict, date: str, shift_start: str, n_slots: int,
                             slot_min: int = 30) -> list[SlotWeather] | None:
    """Interpolate C1's hourly series to the midpoint of every slot of the shift. None if it doesn't cover it."""
    try:
        times = np.array([dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() for t in hourly["time"]])
        h, m = map(int, shift_start.split(":"))
        day = dt.date.fromisoformat(date)
        start = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=TZ).timestamp()
        mids = start + (np.arange(n_slots) + 0.5) * slot_min * 60
        if mids[0] < times[0] or mids[-1] > times[-1]:
            return None

        def col(name, shift_s=0.0):
            v = np.array([np.nan if x is None else x for x in hourly[name]], dtype=float)
            ok = ~np.isnan(v)
            return np.interp(mids, times[ok] + shift_s, v[ok])
        tdb, rh = col("temperature_2m"), col("relative_humidity_2m")
        wind = col("wind_speed_10m") * WIND_10M_TO_BODY
        solar = col("shortwave_radiation", shift_s=-1800)    # value at hour t = average over the hour before
        wbgt = col("wbgt", shift_s=-1800)
        return [SlotWeather(tdb=round(float(a), 2), rh=round(float(b), 1), solar=round(max(0.0, float(c)), 1),
                            wind=round(max(0.2, float(d)), 2), wbgt=round(float(e), 2))
                for a, b, c, d, e in zip(tdb, rh, solar, wind, wbgt)]
    except Exception as e:
        log.info("could not build slot weather: %s", e)
        return None


def hazard_windows_from_events(events: list[dict], date: str, shift_start: str, n_slots: int,
                               slot_min: int = 30) -> list[HazardWindow]:
    """C1 HazardEvents that overlap the shift -> planner hazard windows."""
    h, m = map(int, shift_start.split(":"))
    day = dt.date.fromisoformat(date)
    start = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)
    end = start + dt.timedelta(minutes=n_slots * slot_min)
    out = []
    for e in events or []:
        if not e.get("starts") and not e.get("ends"):
            continue                     # a current reading (e.g. WBGT now), not a time window
        kind = None
        if e.get("hazard") == "lightning" and (e.get("details") or {}).get("state") in ("stop_work", "all_clear_pending"):
            kind = "lightning"
        elif str(e.get("source", "")).startswith("NWS"):
            title = (e.get("title") or "").lower()
            kind = next((k for word, k in TITLE_KINDS if word in title), None)
        if kind is None:
            continue
        try:
            s = dt.datetime.fromisoformat(e["starts"].replace("Z", "+00:00")) if e.get("starts") else start
            f = dt.datetime.fromisoformat(e["ends"].replace("Z", "+00:00")) if e.get("ends") else end
        except (TypeError, ValueError):
            continue
        if f <= start or s >= end:
            continue
        a = max(0, int((s - start).total_seconds() // (slot_min * 60)))
        b = min(n_slots, -int(-(f - start).total_seconds() // (slot_min * 60)))
        if b > a:
            out.append(HazardWindow(kind, a, b, source=e.get("source", "C1"), note=e.get("title", "")))
    return out


# ---------------------------------------------------------------------------
# C2: hazard index + past incidents
# ---------------------------------------------------------------------------
def c2_index(site_id: str, date: str, offline: bool = False) -> dict | None:
    """C2's predict_index (offline = cached data only, no network), or None."""
    try:
        from ml.hazard_index.predict import predict_index
        return predict_index(site_id, date, offline=offline)
    except Exception as e:
        log.info("C2 index unavailable for %s: %s", site_id, e)
        return None


def c2_incidents(site_id: str, limit: int = 3) -> list[dict] | None:
    """Real past incidents near the site (Storm Events + OSHA), or None if C2's records aren't available."""
    try:
        from ml.hazard_index.predict import past_incidents
        recs = past_incidents(site_id, limit=limit)
    except Exception as e:
        log.info("C2 incidents unavailable for %s: %s", site_id, e)
        return None
    return [{"date": r["date"], "text": r["text"], "source": "NOAA Storm Events" if r["source"] == "StormEvents"
             else "OSHA Severe Injury Reports" if r["source"] == "OSHA" else r["source"]} for r in recs]
