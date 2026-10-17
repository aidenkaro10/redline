"""
Wildlife sightings near each site from iNaturalist (no key to read).

Taxa (IDs checked live Oct 17, 2026):
    26159   American alligator (Alligator mississippiensis)
    797576  pit vipers (Crotalinae: rattlesnakes, cottonmouths, copperheads)
    30493   coral snakes (Micrurus)
    67598   red imported fire ant (Solenopsis invicta)

ONE paged query for all of Florida over the last WILDLIFE_DAYS days, refreshed
hourly, well under iNaturalist's 60 requests a minute. Sightings are sporadic;
crew photo reports (session A) carry most of this.

Data: observations from iNaturalist.org; each record keeps its observer and
license, and we link back to it. Non-commercial use (CC BY-NC).

    from backend.hazards.wildlife import wildlife_events
    events, feed = wildlife_events(sites)
"""

from datetime import timedelta

from backend.config import WILDLIFE_CLOSE_MI, WILDLIFE_DAYS, WILDLIFE_RADIUS_MI
from backend.hazards import fake, store
from backend.hazards.cache import FeedCache
from backend.hazards.events import HazardEvent, utc_now
from backend.hazards.geo import distance_mi
from backend.hazards.http import get_json

INAT_URL = "https://api.inaturalist.org/v1/observations"
TAXA = {26159: "alligator", 797576: "pit viper", 30493: "coral snake", 67598: "fire ants"}
DANGEROUS_CLOSE = {"alligator", "pit viper", "coral snake"}
MAX_PAGES = 5

wildlife_cache = FeedCache("inat_fl", max_age_s=3600)


def group_of(obs):
    """Which of our groups an iNaturalist observation belongs to."""
    taxon = obs.get("taxon") or {}
    ids = set(taxon.get("ancestor_ids") or []) | {taxon.get("id")}
    for tid, name in TAXA.items():
        if tid in ids:
            return name
    return None


def fetch_sightings(now=None):
    now = now or utc_now()
    d1 = (now - timedelta(days=WILDLIFE_DAYS)).date().isoformat()
    out = []
    for page in range(1, MAX_PAGES + 1):
        d = get_json(INAT_URL, {
            "taxon_id": ",".join(str(t) for t in TAXA), "swlat": 24.3, "swlng": -88.0,
            "nelat": 31.1, "nelng": -79.8, "d1": d1, "per_page": 200, "page": page,
            "order_by": "observed_on", "order": "desc"}, timeout=40)
        for obs in d.get("results", []):
            loc = obs.get("location")
            group = group_of(obs)
            if not loc or not group:
                continue
            lat, lon = (float(x) for x in loc.split(","))
            taxon = obs.get("taxon") or {}
            out.append({"id": obs["id"], "group": group, "lat": lat, "lon": lon,
                        "observed_on": obs.get("observed_on"),
                        "name": taxon.get("preferred_common_name") or taxon.get("name"),
                        "observer": (obs.get("user") or {}).get("login"),
                        "license": obs.get("license_code") or "all rights reserved",
                        "url": obs.get("uri"), "obscured": bool(obs.get("obscured"))})
        if page * 200 >= d.get("total_results", 0):
            break
    return out


def wildlife_events(sites, now=None):
    """One event per site per animal group seen within WILDLIFE_RADIUS_MI."""
    now = now or utc_now()
    if store.is_fake():
        result = fake.feed_result(fake.sightings(sites, now), now)
    else:
        result = wildlife_cache.get(lambda: fetch_sightings(now), now=now)
    events = []
    for site in sites:
        groups = {}
        for s in result.data or []:
            d = distance_mi(site.lat, site.lon, s["lat"], s["lon"])
            if d <= WILDLIFE_RADIUS_MI:
                groups.setdefault(s["group"], []).append((d, s))
        for group, hits in groups.items():
            hits.sort(key=lambda x: x[0])
            d, nearest = hits[0]
            sev = "advisory" if group in DANGEROUS_CLOSE and d <= WILDLIFE_CLOSE_MI else "info"
            n = len(hits)
            events.append(HazardEvent(
                site_id=site.site_id, hazard="wildlife", severity=sev, source="iNaturalist",
                source_id=f"inat-{group}",
                title=f"{n} {group} sighting{'s' if n > 1 else ''} within {WILDLIFE_RADIUS_MI} mi "
                      f"(nearest {d:.1f} mi, {nearest['observed_on']})",
                observed_at=nearest["observed_on"], fetched_at=result.fetched_at, stale=result.stale,
                fake=store.is_fake(),
                details={"group": group, "count": n, "nearest_mi": round(d, 1),
                         "sightings": [dict(s, distance_mi=round(dd, 1)) for dd, s in hits[:5]],
                         "credit": "Observations from iNaturalist.org (observers and licenses listed)"}))
    return events, result
