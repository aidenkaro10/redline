"""
Open-Meteo weather for every site, with WBGT computed for each hour.

ONE call fetches all sites (Open-Meteo takes comma-separated lat/lon lists).
No key. Free tier is non-commercial, CC BY 4.0 data ("Weather data by
Open-Meteo.com"), up to 10,000 calls a day; a call for N sites counts as N,
so we refresh every 30 minutes (~3,800 a day for 79 sites).

Per site we get hourly: temperature, humidity, pressure, 10 m wind, solar
radiation, rain and top-soil moisture, from 24 h ago to 3 days ahead.

    from backend.hazards.weather import site_weather, weather_events
    wx, feed = site_weather(sites)            # {site_id: summary + hourly series}
    events = weather_events(sites, wx, feed)  # heat (WBGT), soil, heavy rain
"""

from datetime import timedelta

import numpy as np

from backend.config import HEAVY_RAIN_24H_MM, SOIL_SATURATED_M3M3, SOIL_WET_M3M3
from backend.hazards import fake, store
from backend.hazards.cache import FeedCache
from backend.hazards.events import HazardEvent, iso, parse_time, utc_now
from backend.hazards.http import get_json
from backend.hazards.wbgt import (FLAG_SEVERITY, cos_zenith, earth_sun_distance_au, wbgt_flag,
                                  wbgt_liljegren, wind_at_2m)
from backend.sites.sites import load_sites

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE = "Open-Meteo"
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "surface_pressure", "wind_speed_10m",
               "shortwave_radiation", "precipitation", "soil_moisture_0_to_1cm",
               "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm"]

weather_cache = FeedCache("openmeteo_forecast", max_age_s=1800)


def fetch_forecast(sites):
    """Raw hourly weather for all sites: {site_id: {"time": [...], var: [...]}}."""
    data = get_json(FORECAST_URL, {
        "latitude": ",".join(f"{s.lat:.4f}" for s in sites),
        "longitude": ",".join(f"{s.lon:.4f}" for s in sites),
        "hourly": ",".join(HOURLY_VARS),
        "wind_speed_unit": "ms", "timezone": "GMT", "past_days": 1, "forecast_days": 3,
    }, timeout=40)
    if isinstance(data, dict):           # a single site comes back as one object
        data = [data]
    out = {}
    for site, block in zip(sites, data):
        h = block["hourly"]
        # Open-Meteo times are UTC hours without a zone: add Z so they parse right
        h["time"] = [t + ":00Z" if len(t) == 16 else t for t in h["time"]]
        out[site.site_id] = h
    return out


def add_wbgt(site, hourly):
    """Compute WBGT (and its flag) for every hour of one site's series, in place."""
    times = [parse_time(t) for t in hourly["time"]]
    n = len(times)

    def col(name, default):
        vals = hourly.get(name) or [None] * n
        return np.array([default if v is None else v for v in vals], dtype=float)

    tair = col("temperature_2m", np.nan)
    rh = col("relative_humidity_2m", np.nan)
    pres = col("surface_pressure", 1013.0)
    wind10 = col("wind_speed_10m", 1.0)
    solar = col("shortwave_radiation", 0.0)
    # Solar is the average over the previous hour: take the sun angle mid-hour.
    mids = [t - timedelta(minutes=30) for t in times]
    cza = np.array([cos_zenith(m, site.lat, site.lon) for m in mids])
    wind2 = np.array([wind_at_2m(w, 10.0, c > 0, s) for w, c, s in zip(wind10, cza, solar)])
    au = earth_sun_distance_au(times[len(times) // 2]) if times else 1.0
    out = wbgt_liljegren(tair, rh, pres, wind2, solar, cza, earth_sun_au=au)
    wbgt = np.round(out["wbgt"], 2)
    hourly["wbgt"] = [None if np.isnan(v) else float(v) for v in wbgt]
    hourly["globe_temp"] = [None if np.isnan(v) else round(float(v), 2) for v in out["tg"]]
    hourly["wbgt_flag"] = [wbgt_flag(v) for v in hourly["wbgt"]]
    return hourly


def heat_index_f(temp_c, rh_pct):
    """NWS heat index (°F) from air temp (°C) and humidity (%): Rothfusz regression
    with the NWS low-humidity and high-humidity adjustments."""
    if temp_c is None or rh_pct is None:
        return None
    t = temp_c * 9 / 5 + 32
    rh = rh_pct
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        return round(simple, 1)
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= (13 - rh) / 4 * ((17 - abs(t - 95)) / 17) ** 0.5
    elif rh > 85 and 80 <= t <= 87:
        hi += (rh - 85) / 10 * (87 - t) / 5
    return round(hi, 1)


def _hour_index(times, now):
    """Index of the latest hour at or before now (the 'current' row)."""
    idx = 0
    for i, t in enumerate(times):
        if t <= now:
            idx = i
    return idx


def _max_in(values, times, start, end):
    """(max value, time) over [start, end), ignoring missing values."""
    best, best_t = None, None
    for v, t in zip(values, times):
        if v is not None and start <= t < end and (best is None or v > best):
            best, best_t = v, t
    return best, iso(best_t)


def summarize(site, hourly, now):
    """The numbers the map, band and briefing need for one site."""
    times = [parse_time(t) for t in hourly["time"]]
    i = _hour_index(times, now)
    soil = [hourly.get(k, [None] * len(times))[i] for k in
            ("soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm")]
    soil = [v for v in soil if v is not None]
    rain24 = sum(v or 0 for v, t in zip(hourly.get("precipitation", []), times)
                 if now - timedelta(hours=24) < t <= now)
    # Florida local days (Eastern daylight time, UTC-4) for "today" and "tomorrow"
    local_midnight = (now - timedelta(hours=4)).replace(hour=0, minute=0, second=0, microsecond=0) \
        + timedelta(hours=4)
    tmr = local_midnight + timedelta(days=1)
    today_max = _max_in(hourly["wbgt"], times, local_midnight, tmr)
    tomorrow_max = _max_in(hourly["wbgt"], times, tmr, tmr + timedelta(days=1))
    next12 = _max_in(hourly["wbgt"], times, now, now + timedelta(hours=12))
    pick = lambda k: hourly.get(k, [None] * len(times))[i]  # noqa: E731
    return {
        "time": hourly["time"][i],
        "temp_c": pick("temperature_2m"),
        "rh_pct": pick("relative_humidity_2m"),
        "wind_ms_10m": pick("wind_speed_10m"),
        "solar_wm2": pick("shortwave_radiation"),
        "wbgt_c": pick("wbgt"),
        "globe_c": pick("globe_temp"),
        "wbgt_flag": pick("wbgt_flag"),
        "heat_index_f": heat_index_f(pick("temperature_2m"), pick("relative_humidity_2m")),
        "soil_moisture_m3m3": round(max(soil), 3) if soil else None,
        "rain_24h_mm": round(rain24, 1),
        "wbgt_max_next_12h": {"wbgt_c": next12[0], "at": next12[1], "flag": wbgt_flag(next12[0])},
        "wbgt_max_today": {"wbgt_c": today_max[0], "at": today_max[1], "flag": wbgt_flag(today_max[0])},
        "wbgt_max_tomorrow": {"wbgt_c": tomorrow_max[0], "at": tomorrow_max[1],
                              "flag": wbgt_flag(tomorrow_max[0])},
    }


def site_weather(sites, now=None):
    """({site_id: {"now": summary, "hourly": series}}, FeedResult) for all sites."""
    now = now or utc_now()
    if store.is_fake():
        result = fake.feed_result(fake.weather(sites, now), now)
    else:
        # Always fetch every site, so the shared cache never holds a partial list
        result = weather_cache.get(lambda: fetch_forecast(list(load_sites())), now=now)
    out = {}
    by_id = {s.site_id: s for s in sites}
    for sid, hourly in (result.data or {}).items():
        site = by_id.get(sid)
        if site is None or not hourly.get("time"):
            continue
        hourly = dict(hourly)
        add_wbgt(site, hourly)
        out[sid] = {"now": summarize(site, hourly, now), "hourly": hourly}
    return out, result


def weather_events(sites, wx, result):
    """Heat (WBGT flag), soil saturation and heavy-rain events from the weather."""
    events = []
    common = dict(source=SOURCE, fetched_at=result.fetched_at, stale=result.stale,
                  fake=store.is_fake())
    for site in sites:
        w = wx.get(site.site_id, {}).get("now")
        if not w:
            continue
        flag = w["wbgt_flag"]
        sev = FLAG_SEVERITY.get(flag)
        if sev and sev != "info":
            events.append(HazardEvent(
                site_id=site.site_id, hazard="heat", severity=sev,
                title=f"WBGT {w['wbgt_c']:.1f} °C ({flag} flag)", source_id="wbgt-now",
                observed_at=w["time"],
                details={"wbgt_c": w["wbgt_c"], "flag": flag, "temp_c": w["temp_c"],
                         "rh_pct": w["rh_pct"], "next_12h": w["wbgt_max_next_12h"]}, **common))
        soil = w["soil_moisture_m3m3"]
        if soil is not None and soil >= SOIL_WET_M3M3:
            sev = "warning" if soil >= SOIL_SATURATED_M3M3 else "advisory"
            label = "Saturated ground" if sev == "warning" else "Wet ground"
            events.append(HazardEvent(
                site_id=site.site_id, hazard="soil", severity=sev,
                title=f"{label} (soil moisture {soil:.2f} m³/m³)", source_id="soil-now",
                observed_at=w["time"], details={"soil_moisture_m3m3": soil}, **common))
        if w["rain_24h_mm"] >= HEAVY_RAIN_24H_MM:
            events.append(HazardEvent(
                site_id=site.site_id, hazard="flood", severity="advisory",
                title=f"Heavy rain: {w['rain_24h_mm']:.0f} mm in 24 h", source_id="rain-24h",
                observed_at=w["time"], details={"rain_24h_mm": w["rain_24h_mm"]}, **common))
    return events
