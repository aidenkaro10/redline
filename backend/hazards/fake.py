"""
Fake mode: a built-in demo scenario for every feed, no internet needed.

Turn it on with HAZARD_MODE=fake in .env, or store.set_mode("fake") at runtime.
Each function returns data in the SAME shape the real feed returns, so all
the matching, rule and ranking code runs exactly as it does live.

The scenario (a stormy Florida summer afternoon):
- Flood Warning on the St. Johns River at Seminole (eia-136), river in minor flood.
- Heat Advisory around Tampa Bay (Big Bend, eia-645), WBGT in the red/black flag.
- A thunderstorm cell drifting past Crystal River (eia-628): flashes for the
  first 20 minutes of every hour, then the 30-minute all-clear countdown runs.
- Red Flag Warning plus a satellite fire detection near the Pensacola site (eia-641).
- Saturated soil and an alligator sighting near Stanton (eia-564).
"""

import math
from datetime import timedelta

from backend.hazards.cache import FeedResult
from backend.hazards.events import iso

FLOOD_SITE = "eia-136"       # Seminole, Putnam County, on the St. Johns River
HEAT_SITE = "eia-645"        # Big Bend, Tampa Bay
STORM_SITE = "eia-628"       # Crystal River
FIRE_SITE = "eia-641"        # Gulf Clean Energy Center, Pensacola
SOIL_SITE = "eia-564"        # Stanton Energy Center, Orlando


def feed_result(data, now=None):
    from backend.hazards.events import utc_now
    return FeedResult(data, iso(now or utc_now()), stale=False)


def _site(sites, site_id):
    return next((s for s in sites if s.site_id == site_id), None)


def _box(site, half=0.15):
    """A small square polygon (GeoJSON) around a site."""
    lat, lon = site.lat, site.lon
    ring = [[lon - half, lat - half], [lon + half, lat - half], [lon + half, lat + half],
            [lon - half, lat + half], [lon - half, lat - half]]
    return {"type": "Polygon", "coordinates": [ring]}


def _alert(aid, event, site, now, hours, severity="Severe"):
    return {
        "id": f"fake-{aid}", "event": event, "headline": f"{event} (demo data)",
        "severity": severity, "urgency": "Immediate", "certainty": "Observed",
        "sent": iso(now - timedelta(minutes=40)), "onset": iso(now - timedelta(minutes=40)),
        "ends": iso(now + timedelta(hours=hours)), "sender": "NWS (fake mode)",
        "ugc": [], "vtec": [], "geometry": _box(site), "description": "Demo data.",
        "instruction": "",
    }


def nws_alerts(sites, context, now):
    """Fake NWS Florida alert list (same shape as nws.fetch_fl_alerts)."""
    out = []
    for aid, event, sid, hours in [
        ("flood", "Flood Warning", FLOOD_SITE, 30),
        ("heat", "Heat Advisory", HEAT_SITE, 6),
        ("redflag", "Red Flag Warning", FIRE_SITE, 8),
        ("tstorm", "Severe Thunderstorm Warning", STORM_SITE, 1),
    ]:
        site = _site(sites, sid)
        if site:
            out.append(_alert(aid, event, site, now, hours))
    return out


def heatrisk(sites, now):
    """Fake HeatRisk: hotter in the south, Tampa Bay at Major."""
    today = (now - timedelta(hours=4)).date()
    out = {}
    for s in sites:
        v = 3 if s.site_id == HEAT_SITE else (2 if s.lat < 28.5 else 1)
        out[s.site_id] = [{"date": (today + timedelta(days=d)).isoformat(), "value": max(0, v - d // 3)}
                          for d in range(7)]
    return out


def storm_flashes(sites, now):
    """Fake GLM flashes: a storm near Crystal River for minutes 0-20 of each hour.

    Returns flashes in the same shape as lightning.fetch_recent_flashes:
    [{"time": ISO, "lat":, "lon":, "quality": 0, "energy": ...}, ...]
    """
    site = _site(sites, STORM_SITE)
    if site is None:
        return []
    out = []
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    t = hour_start
    i = 0
    while t <= now and t < hour_start + timedelta(minutes=20):
        # The cell drifts from 14 miles west to 4 miles east over 20 minutes
        frac = (t - hour_start).total_seconds() / 1200
        miles_east = -14 + 18 * frac
        lon = site.lon + miles_east / (69.0 * math.cos(math.radians(site.lat)))
        lat = site.lat + 0.03 * math.sin(i)
        out.append({"time": iso(t), "lat": round(lat, 4), "lon": round(lon, 4),
                    "quality": 0, "energy": 1e-13})
        t += timedelta(seconds=40)
        i += 1
    return out
