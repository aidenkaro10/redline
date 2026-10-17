"""
Shapes for the frontend contract (docs/interfaces/frontend-contract.md on the
frontend branch): Site, SiteStatus and Hazard. Session A can send these
straight to the UI.

    from backend.hazards.contract import site_records, site_statuses, test_alert
    site_records()                     # GET /api/sites
    site_statuses(pic)                 # one SiteStatus per site, for `site_status` events
    test_alert("LIGHTNING", "eia-663") # POST /api/demo/test-alert

Contract rules: times are Unix seconds, distances in miles, and every
SiteStatus has exactly six hazards in this order:
heat, lightning, flood, fire, soil, wildlife.
"""

from backend.hazards.events import parse_time
from backend.hazards.inject import clear_injected, inject_test_event
from backend.sites.build_context import load_context
from backend.sites.sites import load_sites

KINDS = ("heat", "lightning", "flood", "fire", "soil", "wildlife")
LEVELS = {None: "none", "info": "low", "advisory": "moderate", "warning": "high", "danger": "extreme"}
DEFAULT_DEMO_SITE = "eia-663"       # Deerhaven Generating Station, Gainesville (the crew's site)


def unix(ts):
    """ISO time -> Unix seconds (float), or None."""
    dt = parse_time(ts) if ts else None
    return dt.timestamp() if dt else None


def site_records():
    """Site list for GET /api/sites."""
    ctx = load_context()
    return [{"id": s.site_id, "name": s.name, "operator": s.operator, "tech": s.fuel,
             "capacity_mw": s.capacity_mw, "lat": s.lat, "lon": s.lon, "county": s.county,
             "coastal": bool(ctx.get(s.site_id, {}).get("tides"))} for s in load_sites()]


def _hazard(kind, events, entry):
    """One Hazard object for a kind: the worst event of that kind, or 'none'."""
    evs = [e for e in events if e["hazard"] == kind]
    top = evs[0] if evs else None          # events are sorted worst first
    lightning = entry.get("lightning") or {}
    h = {
        "kind": kind,
        "level": LEVELS[top["severity"] if top else None],
        "headline": top["title"] if top else "No hazard",
        "detail": "; ".join(e["title"] for e in evs[1:]) or None,
        "source": top["source"] if top else None,
        "updated_at": unix(top["fetched_at"]) if top else None,
        "stop_work": kind == "lightning" and lightning.get("state") == "stop_work",
        "band_pattern": top["band_pattern"] if top else None,
        "stale": any(e["stale"] for e in evs),
        "fake": any(e["fake"] for e in evs),
    }
    if kind == "lightning":
        h.update({
            "nearest_miles": lightning.get("nearest_mi_30min"),
            "last_strike_at": unix(lightning.get("last_strike_at")),
            "all_clear_at": unix(lightning.get("all_clear_at")),
            "strikes": [{"lat": s["lat"], "lon": s["lon"], "at": unix(s["at"]), "miles": s["miles"]}
                        for s in lightning.get("strikes", [])],
        })
    if kind == "heat":
        wx = entry.get("weather") or {}
        h.update({"wbgt_c": wx.get("wbgt_c"), "heat_index_f": wx.get("heat_index_f"),
                  "wbgt_flag": wx.get("wbgt_flag"),
                  "heat_risk": (entry.get("heat_risk") or {}).get("today")})
    return h


def site_status(entry, generated_at=None, worker_flag=None, workers_on_site=None):
    """SiteStatus for one entry of hazard_picture()["sites"].

    worker_flag / workers_on_site come from the live system (session A);
    index_tomorrow / index_drivers from the hazard index model (session C2).
    """
    events = entry["events"]
    index = entry.get("hazard_index") or {}
    return {
        "site_id": entry["site"]["site_id"],
        "rank": entry["rank"],
        "score": entry["score"],
        "hazards": [_hazard(k, events, entry) for k in KINDS],
        # NWS alerts that aren't one of the six kinds (wind, tornado, tropical)
        "other_alerts": [{"headline": e["title"], "level": LEVELS[e["severity"]],
                          "band_pattern": e["band_pattern"], "ends": unix(e["ends"])}
                         for e in events if e["hazard"] == "weather"],
        "band_pattern": entry["band_pattern"],
        "index_tomorrow": index.get("index") if isinstance(index, dict) else index,
        "index_drivers": index.get("drivers", []) if isinstance(index, dict) else [],
        "worker_flag": worker_flag,
        "workers_on_site": workers_on_site,
        "static_flood": entry["static"]["summary"],
        "updated_at": unix(generated_at),
    }


def site_statuses(pic):
    """SiteStatus for every site, ranked worst first."""
    return [site_status(e, pic["generated_at"]) for e in pic["sites"]]


def test_alert(kind, site_id=None, now=None):
    """Demo control: POST /api/demo/test-alert {kind: LIGHTNING | FLOOD_FIRE, site_id?}."""
    site_id = site_id or DEFAULT_DEMO_SITE
    if kind == "LIGHTNING":
        return inject_test_event(site_id, "lightning", distance_mi=3.0, now=now)
    if kind == "FLOOD_FIRE":
        return inject_test_event(site_id, "flood", severity="warning", minutes=15,
                                 title="TEST: Flash Flood Warning", now=now)
    raise ValueError(f"unknown test alert kind {kind!r}")


def clear_test_hazards():
    """Demo control: POST /api/demo/clear-hazards."""
    clear_injected()


# Not a pytest test, despite the name
test_alert.__test__ = False
