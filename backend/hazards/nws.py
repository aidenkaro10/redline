"""
National Weather Service: active alerts for every site, plus HeatRisk.

Alerts: ONE call for all of Florida (api.weather.gov/alerts/active?area=FL),
then each alert is matched to sites by NWS zone code (looked up once per site
and saved in backend/sites/site_context.json) or by the alert's polygon.
No key, but NWS requires a User-Agent with contact info (NWS_USER_AGENT).

HeatRisk: NWS's experimental 0-4 heat risk, read for all sites in one call
from its ArcGIS image service. It's a display label only, never a trigger.

    from backend.hazards.nws import alert_events, heatrisk_by_site
    events, feed = alert_events(sites)        # list of HazardEvent, FeedResult
    risk, feed = heatrisk_by_site(sites)      # {site_id: {...}}, FeedResult
"""

import json
from datetime import datetime, timedelta, timezone

from backend.hazards import fake, store
from backend.hazards.cache import FeedCache
from backend.hazards.events import HazardEvent, iso, parse_time, utc_now
from backend.hazards.geo import point_in_geometry
from backend.hazards.http import get_json, get_text

ALERTS_URL = "https://api.weather.gov/alerts/active"
POINTS_URL = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
HEATRISK_URL = ("https://mapservices.weather.noaa.gov/experimental/rest/services/"
                "NWS_HeatRisk/ImageServer/getSamples")
SOURCE = "NWS"

alerts_cache = FeedCache("nws_alerts_fl", max_age_s=120)      # every 2 minutes
heatrisk_cache = FeedCache("nws_heatrisk", max_age_s=3600)    # updates hourly

# ---------------------------------------------------------------------------
# Which hazard and how bad, for each NWS alert type.
# NWS renamed Excessive Heat (VTEC code EH) to Extreme Heat (XH) in March 2025,
# so both names and both codes map to extreme heat.
# ---------------------------------------------------------------------------
EVENT_MAP = {
    "Extreme Heat Warning": ("heat", "danger"),
    "Excessive Heat Warning": ("heat", "danger"),
    "Heat Advisory": ("heat", "warning"),
    "Extreme Heat Watch": ("heat", "advisory"),
    "Excessive Heat Watch": ("heat", "advisory"),
    "Flash Flood Emergency": ("flood", "danger"),
    "Flash Flood Warning": ("flood", "danger"),
    "Flood Warning": ("flood", "warning"),
    "Coastal Flood Warning": ("flood", "warning"),
    "Storm Surge Warning": ("flood", "danger"),
    "Storm Surge Watch": ("flood", "warning"),
    "Flash Flood Watch": ("flood", "advisory"),
    "Flood Watch": ("flood", "advisory"),
    "Coastal Flood Watch": ("flood", "advisory"),
    "Flood Advisory": ("flood", "advisory"),
    "Coastal Flood Advisory": ("flood", "advisory"),
    "Hydrologic Outlook": ("flood", "info"),
    "Fire Warning": ("fire", "danger"),
    "Extreme Fire Danger": ("fire", "warning"),
    "Red Flag Warning": ("fire", "warning"),
    "Fire Weather Watch": ("fire", "advisory"),
    "Tornado Warning": ("weather", "danger"),
    "Extreme Wind Warning": ("weather", "danger"),
    "Hurricane Warning": ("weather", "danger"),
    "Tornado Watch": ("weather", "warning"),
    "Hurricane Watch": ("weather", "warning"),
    "Tropical Storm Warning": ("weather", "warning"),
    "Severe Thunderstorm Warning": ("weather", "warning"),
    "High Wind Warning": ("weather", "warning"),
    "Severe Thunderstorm Watch": ("weather", "advisory"),
    "Tropical Storm Watch": ("weather", "advisory"),
    "Wind Advisory": ("weather", "advisory"),
    "Dense Fog Advisory": ("weather", "advisory"),
    "Special Weather Statement": ("weather", "info"),
}
VTEC_MAP = {"XH": "Extreme Heat", "EH": "Extreme Heat", "HT": "Heat Advisory"}

# Beach and marine products don't matter to a crew at a power plant
IGNORED_EVENTS = {
    "Rip Current Statement", "High Surf Advisory", "High Surf Warning", "Beach Hazards Statement",
    "Small Craft Advisory", "Gale Warning", "Marine Weather Statement", "Hazardous Seas Warning",
    "Coastal Hazard Message", "Air Quality Alert", "Test Message",
}
NWS_SEVERITY_FALLBACK = {"Extreme": "danger", "Severe": "warning", "Moderate": "advisory",
                         "Minor": "info", "Unknown": "info"}


def classify(alert):
    """(hazard, severity) for a simplified alert dict, or None to ignore it."""
    name = alert["event"]
    if name in IGNORED_EVENTS:
        return None
    if name in EVENT_MAP:
        return EVENT_MAP[name]
    # Fall back on the VTEC code (e.g. "/O.NEW.KJAX.XH.W.0001.../")
    for vtec in alert.get("vtec", []):
        parts = vtec.strip("/").split(".")
        if len(parts) > 4 and parts[3] in VTEC_MAP:
            kind = "Warning" if parts[4] == "W" else "Watch" if parts[4] == "A" else "Advisory"
            return EVENT_MAP.get(f"{VTEC_MAP[parts[3]]} {kind}", ("heat", "warning"))
    return ("weather", NWS_SEVERITY_FALLBACK.get(alert.get("severity"), "info"))


def simplify_alert(feature):
    """Keep just the fields we use from one NWS GeoJSON alert feature."""
    p = feature["properties"]
    return {
        "id": p.get("id") or feature.get("id"),
        "event": p.get("event", ""),
        "headline": p.get("headline") or p.get("event", ""),
        "severity": p.get("severity"),
        "urgency": p.get("urgency"),
        "certainty": p.get("certainty"),
        "sent": p.get("sent"),
        "onset": p.get("onset") or p.get("effective"),
        "ends": p.get("ends") or p.get("expires"),
        "sender": p.get("senderName"),
        "ugc": p.get("geocode", {}).get("UGC", []),
        "vtec": p.get("parameters", {}).get("VTEC", []),
        "geometry": feature.get("geometry"),
        "description": (p.get("description") or "")[:600],
        "instruction": (p.get("instruction") or "")[:400],
    }


def fetch_fl_alerts():
    """All active NWS alerts for Florida, simplified. One HTTP call."""
    data = get_json(ALERTS_URL, {"area": "FL"}, headers={"Accept": "application/geo+json"})
    return [simplify_alert(f) for f in data.get("features", [])]


def lookup_site_zones(site):
    """NWS zone codes for a site (run once by build_context). One /points call."""
    p = get_json(POINTS_URL.format(lat=site.lat, lon=site.lon))["properties"]
    last = lambda url: url.rstrip("/").rsplit("/", 1)[-1] if url else None  # noqa: E731
    return {"forecast": last(p.get("forecastZone")), "county": last(p.get("county")),
            "fire": last(p.get("fireWeatherZone")), "cwa": p.get("cwa")}


def alert_hits_site(alert, site, zones):
    """True if this alert covers the site, by zone code or by its polygon."""
    if alert.get("geometry") and point_in_geometry(site.lat, site.lon, alert["geometry"]):
        return True
    codes = {z for z in (zones or {}).values() if z}
    return bool(codes & set(alert.get("ugc", [])))


def alert_events(sites, context=None, now=None):
    """HazardEvents for every NWS alert covering any site. Returns (events, FeedResult)."""
    from backend.sites.build_context import load_context
    now = now or utc_now()
    context = context if context is not None else load_context()
    if store.is_fake():
        result = fake.feed_result(fake.nws_alerts(sites, context, now))
    else:
        result = alerts_cache.get(fetch_fl_alerts, now=now)
    events = []
    for alert in result.data or []:
        kind = classify(alert)
        if kind is None:
            continue
        ends = parse_time(alert.get("ends"))
        if ends and ends < now:
            continue                       # expired while we were offline
        hazard, severity = kind
        for site in sites:
            zones = context.get(site.site_id, {}).get("zones")
            if not alert_hits_site(alert, site, zones):
                continue
            events.append(HazardEvent(
                site_id=site.site_id, hazard=hazard, severity=severity,
                title=alert["event"], source=SOURCE, source_id=alert["id"],
                starts=alert.get("onset"), ends=alert.get("ends"), observed_at=alert.get("sent"),
                fetched_at=result.fetched_at, stale=result.stale, fake=store.is_fake(),
                details={"headline": alert["headline"], "nws_severity": alert.get("severity"),
                         "urgency": alert.get("urgency"), "sender": alert.get("sender"),
                         "instruction": alert.get("instruction")},
            ))
    return events, result


# ---------------------------------------------------------------------------
# HeatRisk (display label only)
# ---------------------------------------------------------------------------
HEATRISK_LABELS = {0: "Little or none", 1: "Minor", 2: "Moderate", 3: "Major", 4: "Extreme"}
HEATRISK_COLORS = {0: "green", 1: "yellow", 2: "orange", 3: "red", 4: "magenta"}


def fetch_heatrisk(sites):
    """{site_id: [{"date": "YYYY-MM-DD", "value": 0-4}, ...]} for up to 7 days. One call."""
    geometry = {"points": [[s.lon, s.lat] for s in sites], "spatialReference": {"wkid": 4326}}
    text = get_text(HEATRISK_URL, {
        "geometry": json.dumps(geometry), "geometryType": "esriGeometryMultipoint",
        "returnFirstValueOnly": "false", "outFields": "idp_validtime", "f": "json",
    })
    data = json.loads(text)
    if "error" in data:
        raise RuntimeError(data["error"])
    out = {s.site_id: {} for s in sites}
    for sample in data.get("samples", []):
        site = sites[sample["locationId"]]
        valid_ms = sample["attributes"]["idp_validtime"]
        # HeatRisk days are valid for local (Eastern) days; the timestamp is 12Z-ish.
        day = datetime.fromtimestamp(valid_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            out[site.site_id][day] = int(round(float(sample["value"])))
        except (TypeError, ValueError):
            continue                       # NoData pixel
    return {sid: [{"date": d, "value": v} for d, v in sorted(days.items())]
            for sid, days in out.items()}


def heatrisk_by_site(sites, now=None):
    """Today's and tomorrow's HeatRisk per site, as display labels.

    Returns ({site_id: {"today": {...}, "tomorrow": {...}, "days": [...]}}, FeedResult).
    """
    now = now or utc_now()
    if store.is_fake():
        result = fake.feed_result(fake.heatrisk(sites, now))
    else:
        result = heatrisk_cache.get(lambda: fetch_heatrisk(sites), now=now)
    # "Today" in Florida (Eastern time, roughly UTC-4)
    local_today = (now - timedelta(hours=4)).date()
    out = {}
    for sid, days in (result.data or {}).items():
        by_date = {d["date"]: d["value"] for d in days}

        def label(date):
            v = by_date.get(date.isoformat())
            return None if v is None else {"value": v, "label": HEATRISK_LABELS.get(v),
                                           "color": HEATRISK_COLORS.get(v)}
        out[sid] = {"today": label(local_today),
                    "tomorrow": label(local_today + timedelta(days=1)),
                    "days": days}
    return out, result


def fetch_point_alerts(lat, lon):
    """Single-point check (used to verify the zone matching; not used in the loop)."""
    data = get_json(ALERTS_URL, {"point": f"{lat:.4f},{lon:.4f}"},
                    headers={"Accept": "application/geo+json"})
    return [simplify_alert(f) for f in data.get("features", [])]


__all__ = ["alert_events", "heatrisk_by_site", "classify", "fetch_fl_alerts", "iso"]
