"""
Wildfire: NASA FIRMS satellite fire detections and NIFC current incidents.
(Red Flag Warnings and Fire Weather Watches come from nws.py.)

FIRMS needs a free MAP_KEY (FIRMS_MAP_KEY in .env, request it by email at
https://firms.modaps.eosdis.nasa.gov/api/map_key/). Without a key the FIRMS
part is skipped and the feed says why. ONE call per satellite covers Florida.
Satellites only pass a few times a day, and Florida has lots of prescribed
and farm burns, so a detection near a site is an advisory unless it's close
and high-confidence.

NIFC (no key): current wildfire incident points, refreshed every 5 minutes.

    from backend.hazards.wildfire import wildfire_events
    events, feeds = wildfire_events(sites)
"""

import csv
import io
import os
from datetime import datetime, timedelta, timezone

from backend.config import FIRE_DETECTION_ADVISORY_MI, FIRE_DETECTION_WARNING_MI, FIRE_INCIDENT_WARNING_MI
from backend.hazards import fake, store
from backend.hazards.cache import FeedCache, FeedResult
from backend.hazards.events import HazardEvent, iso, utc_now
from backend.hazards.geo import distance_mi
from backend.hazards.http import get_json, get_text

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/-88.0,24.3,-79.8,31.1/1"
FIRMS_SOURCES = ("VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_SNPP_NRT")
NIFC_URL = ("https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
            "WFIGS_Incident_Locations_Current/FeatureServer/0/query")

firms_cache = FeedCache("firms_fl", max_age_s=600)
nifc_cache = FeedCache("nifc_fl", max_age_s=300)


def firms_key():
    return os.getenv("FIRMS_MAP_KEY", "").strip()


def parse_firms_csv(text, source):
    """FIRMS CSV -> [{"lat", "lon", "time": ISO, "confidence": "l/n/h", "frp", "source"}]."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            hhmm = row["acq_time"].zfill(4)
            t = datetime.strptime(f"{row['acq_date']} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
            out.append({"lat": float(row["latitude"]), "lon": float(row["longitude"]), "time": iso(t),
                        "confidence": str(row.get("confidence", "")).strip().lower()[:1],
                        "frp": float(row.get("frp") or 0), "source": source})
        except (KeyError, ValueError):
            continue           # header noise or an error line
    return out


def fetch_firms():
    key = firms_key()
    out = []
    for source in FIRMS_SOURCES:
        text = get_text(FIRMS_URL.format(key=key, source=source), timeout=40)
        if text.lower().startswith("invalid") or "error" in text[:100].lower():
            raise RuntimeError(f"FIRMS said: {text[:120]}")
        out += parse_firms_csv(text, source)
    return out


def fetch_nifc():
    d = get_json(NIFC_URL, {
        "where": "POOState='US-FL'",
        "outFields": "IncidentName,IncidentSize,PercentContained,FireDiscoveryDateTime,"
                     "IncidentTypeCategory,UniqueFireIdentifier,ModifiedOnDateTime",
        "f": "geojson"}, timeout=40)
    out = []
    for f in d.get("features", []):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        disc = p.get("FireDiscoveryDateTime")
        out.append({"id": p.get("UniqueFireIdentifier"), "name": p.get("IncidentName"),
                    "lat": lat, "lon": lon, "acres": p.get("IncidentSize"),
                    "contained_pct": p.get("PercentContained"), "type": p.get("IncidentTypeCategory"),
                    "discovered": iso(datetime.fromtimestamp(disc / 1000, tz=timezone.utc)) if disc else None})
    return out


def wildfire_events(sites, now=None):
    """(events, {"firms": FeedResult, "nifc": FeedResult})."""
    now = now or utc_now()
    if store.is_fake():
        firms = fake.feed_result(fake.fire_detections(sites, now), now)
        nifc = fake.feed_result([], now)
    else:
        if firms_key():
            firms = firms_cache.get(fetch_firms, now=now)
        else:
            firms = FeedResult([], None, stale=True, error="FIRMS_MAP_KEY not set in .env")
        nifc = nifc_cache.get(fetch_nifc, now=now)
    events = []
    cutoff = now - timedelta(hours=24)
    for site in sites:
        # Satellite detections: one event per site for the closest one
        close = []
        for det in firms.data or []:
            if datetime.fromisoformat(det["time"].replace("Z", "+00:00")) < cutoff:
                continue
            d = distance_mi(site.lat, site.lon, det["lat"], det["lon"])
            if d <= FIRE_DETECTION_ADVISORY_MI:
                close.append((d, det))
        if close:
            d, det = min(close, key=lambda x: x[0])
            sev = "warning" if d <= FIRE_DETECTION_WARNING_MI and det["confidence"] == "h" else "advisory"
            events.append(HazardEvent(
                site_id=site.site_id, hazard="fire", severity=sev, source="NASA FIRMS",
                source_id="firms-nearest", title=f"Satellite fire detection {d:.1f} mi away",
                observed_at=det["time"], fetched_at=firms.fetched_at, stale=firms.stale,
                fake=store.is_fake(),
                details={"distance_mi": round(d, 1), "detections_within_range": len(close), **det}))
        # NIFC incidents
        for inc in nifc.data or []:
            d = distance_mi(site.lat, site.lon, inc["lat"], inc["lon"])
            if d > FIRE_INCIDENT_WARNING_MI:
                continue
            contained = (inc.get("contained_pct") or 0) >= 100
            if inc.get("type") == "RX" or contained:
                sev, what = "info", ("Prescribed burn" if inc.get("type") == "RX" else "Contained fire")
            else:
                sev, what = "warning", "Active wildfire"
            events.append(HazardEvent(
                site_id=site.site_id, hazard="fire", severity=sev, source="NIFC",
                source_id=inc["id"] or inc["name"], title=f"{what} {inc['name']} {d:.1f} mi away",
                observed_at=inc.get("discovered"), fetched_at=nifc.fetched_at, stale=nifc.stale,
                fake=store.is_fake(), details={"distance_mi": round(d, 1), **inc}))
    return events, {"firms": firms, "nifc": nifc}
