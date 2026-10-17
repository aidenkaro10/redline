"""
Flooding: NWS river gauges (NWPS) and NOAA tide stations near each site.
(NWS flood alerts come from nws.py.)

River gauges
    Each site's nearest gauge with flood stages (within RIVER_GAUGE_MAX_DIST_MI)
    is looked up once and saved in backend/sites/site_context.json. Live, ONE
    call returns every Florida gauge with its current and forecast flood
    category (action, minor, moderate, major). No key.
    https://api.water.noaa.gov/nwps/v1/gauges

Tides (coastal sites only)
    Nearest NOAA water-level station within TIDE_STATION_MAX_DIST_MI, with its
    flood levels, looked up once. Live, the latest 6-minute water level is read
    with datum=STND, the same datum as the flood levels, so they compare directly.
    https://api.tidesandcurrents.noaa.gov

    from backend.hazards.flood import flood_events
    events, info, feeds = flood_events(sites)
"""

from datetime import timedelta
from functools import lru_cache

from backend.config import FLOOD_FORECAST_HOURS, RIVER_GAUGE_MAX_DIST_MI, TIDE_STATION_MAX_DIST_MI
from backend.hazards import fake, store
from backend.hazards.cache import FeedCache
from backend.hazards.events import HazardEvent, iso, parse_time, utc_now
from backend.hazards.geo import distance_mi
from backend.hazards.http import get_json

NWPS_GAUGES_URL = "https://api.water.noaa.gov/nwps/v1/gauges"
NWPS_GAUGE_URL = "https://api.water.noaa.gov/nwps/v1/gauges/{lid}"
FL_BBOX = {"bbox.xmin": -88.0, "bbox.ymin": 24.3, "bbox.xmax": -79.8, "bbox.ymax": 31.1,
           "srid": "EPSG_4326"}
TIDE_STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
TIDE_FLOOD_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{sid}/floodlevels.json"
TIDE_DATA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"

gauges_cache = FeedCache("nwps_gauges_fl", max_age_s=600)     # gauges report every 15-60 min
tides_cache = FeedCache("coops_water_levels", max_age_s=600)   # 6-minute data

CATEGORY_ORDER = {"action": 1, "minor": 2, "moderate": 3, "major": 4}
OBSERVED_SEVERITY = {"action": "advisory", "minor": "warning", "moderate": "danger", "major": "danger"}
# Forecasts are for planning (map, 6 PM briefing), so they never buzz the band
FORECAST_SEVERITY = {"minor": "advisory", "moderate": "advisory", "major": "advisory"}
SKIP_GAUGE_STATUS = {"not_defined", "out_of_service"}


# ---------------------------------------------------------------------------
# One-time lookups (run by backend.sites.build_context)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _all_fl_gauges():
    return tuple(get_json(NWPS_GAUGES_URL, FL_BBOX, timeout=40)["gauges"])


def lookup_nearest_gauge(site):
    """Nearest river gauge with flood stages, or None if none within range."""
    candidates = []
    for g in _all_fl_gauges():
        if g["status"]["observed"]["floodCategory"] in SKIP_GAUGE_STATUS:
            continue
        d = distance_mi(site.lat, site.lon, g["latitude"], g["longitude"])
        if d <= RIVER_GAUGE_MAX_DIST_MI:
            candidates.append((d, g))
    for d, g in sorted(candidates, key=lambda x: x[0])[:4]:
        detail = get_json(NWPS_GAUGE_URL.format(lid=g["lid"]))
        cats = (detail.get("flood") or {}).get("categories") or {}
        stages = {k: v["stage"] for k, v in cats.items() if v.get("stage", -9999) > -9999}
        if "minor" not in stages:
            continue                      # no flood stage defined: useless for us
        return {"lid": g["lid"], "name": g["name"], "lat": g["latitude"], "lon": g["longitude"],
                "distance_mi": round(d, 1), "stages": stages,
                "units": (detail.get("flood") or {}).get("stageUnits", "ft")}
    return None


@lru_cache(maxsize=1)
def _all_tide_stations():
    return tuple(get_json(TIDE_STATIONS_URL, {"type": "waterlevels"}, timeout=40)["stations"])


def lookup_nearest_tide_station(site):
    """Nearest NOAA water-level station with flood levels, or None if not coastal."""
    candidates = []
    for s in _all_tide_stations():
        d = distance_mi(site.lat, site.lon, float(s["lat"]), float(s["lng"]))
        if d <= TIDE_STATION_MAX_DIST_MI:
            candidates.append((d, s))
    for d, s in sorted(candidates, key=lambda x: x[0])[:3]:
        levels = get_json(TIDE_FLOOD_URL.format(sid=s["id"]))
        # Prefer the NWS flood categories, fall back to NOS's own
        out = {}
        for cat in ("minor", "moderate", "major"):
            v = levels.get(f"nws_{cat}")
            if v is None:
                v = levels.get(f"nos_{cat}")
            if v is not None:
                out[cat] = float(v)
        if "minor" in out:
            return {"id": s["id"], "name": s["name"], "lat": float(s["lat"]), "lon": float(s["lng"]),
                    "distance_mi": round(d, 1), "flood_levels_ft_stnd": out}
    return None


# ---------------------------------------------------------------------------
# Live feeds
# ---------------------------------------------------------------------------
def fetch_gauge_status():
    """{lid: {"observed": {...}, "forecast": {...}}} for every Florida gauge. One call."""
    gauges = get_json(NWPS_GAUGES_URL, FL_BBOX, timeout=40)["gauges"]
    return {g["lid"]: g["status"] for g in gauges}


def fetch_water_levels(station_ids):
    """{station_id: {"t": ISO, "ft_stnd": float}} latest reading per tide station."""
    out = {}
    for sid in sorted(set(station_ids)):
        d = get_json(TIDE_DATA_URL, {"station": sid, "product": "water_level", "datum": "STND",
                                     "units": "english", "time_zone": "gmt", "format": "json",
                                     "date": "latest", "application": "planit-control"})
        rows = d.get("data") or []
        if rows and rows[-1].get("v") not in (None, ""):
            r = rows[-1]
            out[sid] = {"t": r["t"].replace(" ", "T") + ":00Z", "ft_stnd": float(r["v"]),
                        "preliminary": r.get("q") == "p"}
    return out


def _category_rank(cat):
    return CATEGORY_ORDER.get(cat, 0)


def gauge_events(site, gauge, status, result, now):
    """Events for one site's river gauge."""
    if not gauge or not status:
        return []
    events = []
    common = dict(site_id=site.site_id, hazard="flood", source="NWS NWPS",
                  fetched_at=result.fetched_at, stale=result.stale, fake=store.is_fake())
    obs, fc = status.get("observed", {}), status.get("forecast", {})
    unit = gauge.get("units", "ft")
    where = f"{gauge['name']} ({gauge['distance_mi']} mi)"
    ocat = obs.get("floodCategory")
    if ocat in OBSERVED_SEVERITY:
        label = "action stage" if ocat == "action" else f"{ocat} flood"
        events.append(HazardEvent(
            severity=OBSERVED_SEVERITY[ocat], source_id=f"{gauge['lid']}-obs",
            title=f"River at {label}: {where}", observed_at=obs.get("validTime"),
            details={"gauge": gauge["lid"], "stage": obs.get("primary"), "unit": unit,
                     "category": ocat, "stages": gauge.get("stages")}, **common))
    fcat = fc.get("floodCategory")
    when = parse_time(fc.get("validTime")) if fc.get("validTime", "").startswith("2") else None
    if fcat in FORECAST_SEVERITY and _category_rank(fcat) > _category_rank(ocat):
        soon = when is not None and when <= now + timedelta(hours=FLOOD_FORECAST_HOURS)
        events.append(HazardEvent(
            severity=FORECAST_SEVERITY[fcat] if soon else "info",
            source_id=f"{gauge['lid']}-fcst", starts=iso(when),
            title=f"River forecast to reach {fcat} flood ({fc.get('primary')} {unit}) "
                  f"by {iso(when)}: {where}",
            details={"gauge": gauge["lid"], "forecast_stage": fc.get("primary"), "unit": unit,
                     "category": fcat, "valid_time": iso(when), "stages": gauge.get("stages")},
            **common))
    return events


def tide_events(site, station, reading, result):
    """Events for one coastal site's tide station."""
    if not station or not reading:
        return []
    levels = station["flood_levels_ft_stnd"]
    v = reading["ft_stnd"]
    cat = None
    for c in ("minor", "moderate", "major"):
        if c in levels and v >= levels[c]:
            cat = c
    if cat is None:
        return []
    return [HazardEvent(
        site_id=site.site_id, hazard="flood", severity=OBSERVED_SEVERITY[cat],
        source="NOAA CO-OPS", source_id=f"tide-{station['id']}",
        title=f"Coastal {cat} flooding: {station['name']} at {v:.2f} ft",
        observed_at=reading["t"], fetched_at=result.fetched_at, stale=result.stale,
        fake=store.is_fake(),
        details={"station": station["id"], "water_level_ft_stnd": v, "flood_levels": levels})]


def flood_events(sites, context=None, now=None):
    """River gauge + tide events for every site.

    Returns (events, {site_id: {"river": {...}|None, "tide": {...}|None}}, {"gauges": FeedResult, "tides": FeedResult}).
    """
    from backend.sites.build_context import load_context
    now = now or utc_now()
    context = context if context is not None else load_context()
    stations = [context.get(s.site_id, {}).get("tides") for s in sites]
    station_ids = [st["id"] for st in stations if st]
    if store.is_fake():
        g_result = fake.feed_result(fake.gauge_status(sites, context, now), now)
        t_result = fake.feed_result(fake.water_levels(station_ids, context, now), now)
    else:
        g_result = gauges_cache.get(fetch_gauge_status, now=now)
        t_result = tides_cache.get(lambda: fetch_water_levels(station_ids), now=now)
    statuses = g_result.data or {}
    levels = t_result.data or {}
    events, info = [], {}
    for site in sites:
        ctx = context.get(site.site_id, {})
        gauge, station = ctx.get("gauges"), ctx.get("tides")
        status = statuses.get(gauge["lid"]) if gauge else None
        reading = levels.get(station["id"]) if station else None
        events += gauge_events(site, gauge, status, g_result, now)
        events += tide_events(site, station, reading, t_result)
        info[site.site_id] = {
            "river": None if not gauge else {
                "gauge": gauge["lid"], "name": gauge["name"], "distance_mi": gauge["distance_mi"],
                "observed": (status or {}).get("observed"), "forecast": (status or {}).get("forecast"),
                "stages": gauge.get("stages")},
            "tide": None if not station else {
                "station": station["id"], "name": station["name"],
                "distance_mi": station["distance_mi"], "reading": reading,
                "flood_levels_ft_stnd": station["flood_levels_ft_stnd"]},
        }
    return events, info, {"gauges": g_result, "tides": t_result}
