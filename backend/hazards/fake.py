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

    Returns flashes in the same shape as lightning.parse_glm:
    [{"t": epoch seconds, "lat":, "lon":, "q": 0, "e": energy}, ...]
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
        out.append({"t": t.timestamp(), "lat": round(lat, 4), "lon": round(lon, 4),
                    "q": 0, "e": 1e-13})
        t += timedelta(seconds=40)
        i += 1
    return out


def weather(sites, now):
    """Fake hourly weather (same shape as weather.fetch_forecast): a hot humid
    Florida summer day. Tampa Bay runs hottest, Orlando's soil is saturated,
    and the St. Johns River site had a night of heavy rain."""
    from backend.hazards.wbgt import cos_zenith
    start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)
    times = [start + timedelta(hours=h) for h in range(24 * 4)]
    out = {}
    for s in sites:
        hot = 2.5 if s.site_id == HEAT_SITE else (1.0 if s.lat < 28 else 0.0)
        h = {k: [] for k in ("time", "temperature_2m", "relative_humidity_2m", "surface_pressure",
                             "wind_speed_10m", "shortwave_radiation", "precipitation",
                             "soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm",
                             "soil_moisture_3_to_9cm")}
        for t in times:
            # The heat peaks about an hour from NOW whatever the real time is,
            # so the demo always shows a hot afternoon. (The sun still follows
            # the real clock, so at night WBGT comes from heat and humidity only.)
            hours_from_now = (t - now).total_seconds() / 3600
            day = math.cos((hours_from_now - 1) / 24 * 2 * math.pi)
            cza = cos_zenith(t - timedelta(minutes=30), s.lat, s.lon)
            rain = 0.0
            if s.site_id == FLOOD_SITE and -20 <= (t - now).total_seconds() / 3600 <= -8:
                rain = 7.5          # ~90 mm over the night
            h["time"].append(iso(t))
            h["temperature_2m"].append(round(28.5 + hot + 5.0 * day, 1))
            humid = 12 if s.site_id == HEAT_SITE else 0
            h["relative_humidity_2m"].append(round(70 + humid - 18 * day, 0))
            h["surface_pressure"].append(1013.0)
            h["wind_speed_10m"].append(round(2.5 + 1.0 * max(day, 0), 2))
            h["shortwave_radiation"].append(round(max(0.0, 950 * cza) * (0.4 if rain else 1.0), 0))
            h["precipitation"].append(rain)
            soil = 0.41 if s.site_id in (SOIL_SITE, FLOOD_SITE) else 0.16
            h["soil_moisture_0_to_1cm"].append(soil)
            h["soil_moisture_1_to_3cm"].append(soil)
            h["soil_moisture_3_to_9cm"].append(soil - 0.01)
        out[s.site_id] = h
    return out


def gauge_status(sites, context):
    """Fake NWPS status for each site's gauge: the St. Johns site's gauge is in
    minor flood and forecast to reach moderate tomorrow; everything else is normal."""
    from backend.hazards.events import utc_now
    now = utc_now()
    out = {}
    flood_gauge = (context.get(FLOOD_SITE, {}).get("gauges") or {}).get("lid")
    for s in sites:
        g = context.get(s.site_id, {}).get("gauges")
        if not g:
            continue
        stages = g.get("stages", {})
        if g["lid"] == flood_gauge:     # sites can share a gauge; they all see the flood
            obs = {"primary": stages.get("minor", 2) + 0.2, "floodCategory": "minor"}
            fc = {"primary": stages.get("moderate", 3) + 0.1, "floodCategory": "moderate",
                  "validTime": iso(now + timedelta(hours=20))}
        else:
            obs = {"primary": round(stages.get("action", 2) * 0.6, 2), "floodCategory": "no_flooding"}
            fc = {"primary": -999, "floodCategory": "fcst_not_current", "validTime": "0001-01-01T00:00:00Z"}
        obs.update({"primaryUnit": "ft", "validTime": iso(now - timedelta(minutes=15))})
        fc.update({"primaryUnit": "ft"})
        out[g["lid"]] = {"observed": obs, "forecast": fc}
    return out


def water_levels(station_ids, context):
    """Fake tide readings: every coastal station a foot below minor flood."""
    from backend.hazards.events import utc_now
    now = utc_now()
    levels = {}
    for entry in context.values():
        st = entry.get("tides")
        if st and st["id"] in station_ids:
            levels[st["id"]] = {"t": iso(now - timedelta(minutes=6)),
                                "ft_stnd": round(st["flood_levels_ft_stnd"]["minor"] - 1.0, 2),
                                "preliminary": True}
    return levels


def fire_detections(sites, now):
    """Fake FIRMS hotspot 3 miles north of the Pensacola site, high confidence."""
    site = _site(sites, FIRE_SITE)
    if not site:
        return []
    return [{"lat": round(site.lat + 3 / 69.0, 4), "lon": site.lon,
             "time": iso(now - timedelta(hours=2)), "confidence": "h", "frp": 25.0,
             "source": "VIIRS_NOAA20_NRT (fake)"}]


def sightings(sites, now):
    """Fake iNaturalist sightings: an alligator half a mile from Stanton."""
    site = _site(sites, SOIL_SITE)
    if not site:
        return []
    return [{"id": 1, "group": "alligator", "lat": round(site.lat + 0.5 / 69.0, 5), "lon": site.lon,
             "observed_on": (now - timedelta(days=1)).date().isoformat(), "name": "American Alligator",
             "observer": "demo", "license": "cc-by-nc", "url": "https://www.inaturalist.org",
             "obscured": False}]
