"""
The current hazard picture for every job site, ranked worst first.
One call feeds the map (frontend), the band (alert router) and the 6 PM briefing.

    from backend.hazards.picture import hazard_picture
    pic = hazard_picture()           # dict, JSON-ready
    pic["sites"][0]                  # the most dangerous site right now

From the terminal:
    python -m backend.hazards.picture            # live APIs
    python -m backend.hazards.picture --fake     # built-in demo scenario, no internet
    python -m backend.hazards.picture --top 20 --json

Every feed is cached and survives the WiFi dropping (stale data is marked).
A feed that breaks completely is listed under "feeds" with its error and
the rest of the picture still comes back.
"""

import argparse
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.hazards import store
from backend.hazards.events import SEVERITY_LEVEL, iso, utc_now
from backend.hazards.inject import injected_events
from backend.hazards.report_log import get_log
from backend.sites.build_context import load_context
from backend.sites.sites import load_sites

log = logging.getLogger(__name__)

# When a site has several band-worthy hazards, which pattern wins. A flood or
# fire warning at the site outranks the site heat heads-up. The worker's own
# core-temp alerts (HEAT_RED etc.) are merged in by the alert router (session A).
PATTERN_PRIORITY = ["LIGHTNING", "FLOOD_FIRE", "HEAT_YELLOW"]

# Feed name in the picture -> the "source" its events carry
FEED_SOURCES = {"nws_alerts": "NWS", "open_meteo": "Open-Meteo", "goes19_glm": "GOES-19 GLM",
                "nwps_gauges": "NWS NWPS", "tides": "NOAA CO-OPS", "firms": "NASA FIRMS",
                "nifc": "NIFC", "inaturalist": "iNaturalist"}

_latest = None
_latest_lock = threading.Lock()


def _run_feeds(sites, context, now):
    """Run every feed in parallel. Each returns its events plus extra per-site info."""
    from backend.hazards.flood import flood_events
    from backend.hazards.lightning import lightning_events
    from backend.hazards.nws import alert_events, heatrisk_by_site
    from backend.hazards.weather import site_weather, weather_events
    from backend.hazards.wildfire import wildfire_events
    from backend.hazards.wildlife import wildlife_events

    def nws():
        ev, r = alert_events(sites, context, now)
        return ev, {}, {"nws_alerts": r}

    def heatrisk():
        risk, r = heatrisk_by_site(sites, now)
        return [], {"heat_risk": risk}, {"nws_heatrisk": r}

    def wx():
        data, r = site_weather(sites, now)
        return weather_events(sites, data, r), {"weather": data}, {"open_meteo": r}

    def lightning():
        ev, states, r = lightning_events(sites, now)
        return ev, {"lightning": states}, {"goes19_glm": r}

    def flooding():
        ev, info, feeds = flood_events(sites, context, now)
        return ev, {"flood": info}, {"nwps_gauges": feeds["gauges"], "tides": feeds["tides"]}

    def fire():
        ev, feeds = wildfire_events(sites, now)
        return ev, {}, {"firms": feeds["firms"], "nifc": feeds["nifc"]}

    def animals():
        ev, r = wildlife_events(sites, now)
        return ev, {}, {"inaturalist": r}

    jobs = {"nws_alerts": nws, "nws_heatrisk": heatrisk, "open_meteo": wx, "goes19_glm": lightning,
            "flood": flooding, "wildfire": fire, "wildlife": animals}
    events, extra, feeds = [], {}, {}
    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
        for name, fut in futures.items():
            try:
                ev, ex, fd = fut.result()
                events += ev
                extra.update(ex)
                feeds.update({k: v.to_dict() for k, v in fd.items()})
            except Exception as e:          # a bug or a weird API answer: keep the rest going
                log.exception("hazard feed %s crashed", name)
                feeds[name] = {"fetched_at": None, "stale": True,
                               "error": f"crashed: {type(e).__name__}: {e}"[:300]}
    return events, extra, feeds


STOP_WORK_BONUS = 5     # a lightning stop-work is mandatory, so it outranks other "danger" sites


def _site_score(evs, wbgt, static_risk):
    """Higher = more dangerous. The worst hazard dominates; others and heat break ties."""
    levels = sorted((SEVERITY_LEVEL[e["severity"]] for e in evs), reverse=True)
    top = levels[0] if levels else 0
    others = sum(levels[1:])
    stop = any(e["hazard"] == "lightning" and e["severity"] == "danger" for e in evs)
    return round(10 * top + min(others, 9) + (wbgt or 0) / 10 + 0.5 * (static_risk or 0)
                 + (STOP_WORK_BONUS if stop else 0), 2)


def _band_pattern(evs):
    patterns = {e["band_pattern"] for e in evs if e.get("band_pattern")}
    for p in PATTERN_PRIORITY:
        if p in patterns:
            return p
    return None


def hazard_picture(now=None, sites=None, write_log=True):
    """Build the ranked hazard picture for all sites. See docs/interfaces/site-hazards.md."""
    from backend.hazards.static_context import static_flood_summary
    now = now or utc_now()
    sites = list(sites or load_sites())
    context = load_context()
    events, extra, feeds = _run_feeds(sites, context, now)
    events += [e for e in injected_events(now) if e.site_id in {s.site_id for s in sites}]

    if write_log:
        # Events from feeds that are down aren't marked "cleared" in the log
        # just because we couldn't reach the feed.
        stale_sources = {e.source for e in events if e.stale}
        stale_sources |= {src for name, src in FEED_SOURCES.items()
                          if feeds.get(name, {}).get("stale")}
        try:
            get_log().sync(events, at=now, skip_clear_sources=stale_sources)
        except OSError as e:
            log.warning("could not write hazard log: %s", e)

    by_site = {s.site_id: [] for s in sites}
    for e in events:
        by_site.setdefault(e.site_id, []).append(e.to_dict())

    entries = []
    for s in sites:
        evs = sorted(by_site.get(s.site_id, []), key=lambda e: -e["level"])
        wx = (extra.get("weather", {}).get(s.site_id) or {}).get("now")
        static = static_flood_summary(context.get(s.site_id, {}))
        heat_risk = extra.get("heat_risk", {}).get(s.site_id) or {}
        flood = extra.get("flood", {}).get(s.site_id) or {}
        top = evs[0]["severity"] if evs else None
        entries.append({
            "site": s.to_dict(),
            "score": _site_score(evs, (wx or {}).get("wbgt_c"), static["static_flood_risk"]),
            "top_severity": top,
            "band_pattern": _band_pattern(evs),
            "events": evs,
            "stale": any(e["stale"] for e in evs),
            "weather": wx,
            "heat_risk": {"today": heat_risk.get("today"), "tomorrow": heat_risk.get("tomorrow")},
            "lightning": extra.get("lightning", {}).get(s.site_id),
            "flood": flood,
            "static": {**static, "fema": context.get(s.site_id, {}).get("fema"),
                       "levee": context.get(s.site_id, {}).get("levees")},
            "tomorrow": {
                "wbgt_max": (wx or {}).get("wbgt_max_tomorrow"),
                "heat_risk": heat_risk.get("tomorrow"),
                "river_forecast": (flood.get("river") or {}).get("forecast"),
                "alerts_into_tomorrow": [e["title"] for e in evs if e["source"] == "NWS" and e["ends"]],
            },
            # Filled in by the hazard index model (session C2) when it exists
            "hazard_index": None,
        })
    entries.sort(key=lambda x: (-x["score"], x["site"]["name"]))
    for i, entry in enumerate(entries, 1):
        entry["rank"] = i

    counts = {sev: sum(1 for e in events if e.severity == sev) for sev in SEVERITY_LEVEL}
    pic = {
        "generated_at": iso(now),
        "mode": store.get_mode(),
        "site_count": len(sites),
        "event_counts": counts,
        "feeds": feeds,
        "sites": entries,
        "credits": CREDITS,
    }
    with _latest_lock:
        global _latest
        _latest = pic
    return pic


def latest_picture():
    """The last picture built (no network). None until the first build."""
    with _latest_lock:
        return _latest


def band_commands(pic=None):
    """{site_id: "BUZZ:<PATTERN>"} for sites whose hazards should buzz the band."""
    pic = pic or latest_picture() or hazard_picture()
    return {e["site"]["site_id"]: f"BUZZ:{e['band_pattern']}" for e in pic["sites"] if e["band_pattern"]}


def site_entry(site_id, pic=None):
    """One site's entry from the picture (KeyError if unknown)."""
    pic = pic or latest_picture() or hazard_picture()
    for e in pic["sites"]:
        if e["site"]["site_id"] == site_id:
            return e
    raise KeyError(site_id)


def start_background_refresh(interval_s=60):
    """Rebuild the picture every interval_s seconds in a daemon thread.

    The API can then serve latest_picture() instantly. Returns the thread.
    """
    def loop():
        while True:
            try:
                hazard_picture()
            except Exception:
                log.exception("hazard picture refresh failed")
            time.sleep(interval_s)
    t = threading.Thread(target=loop, name="hazard-refresh", daemon=True)
    t.start()
    return t


CREDITS = [
    "Job sites: U.S. Energy Information Administration, Form EIA-860M (public domain)",
    "Alerts, HeatRisk, river gauges: NOAA National Weather Service",
    "Weather: Open-Meteo.com (CC BY 4.0)",
    "Lightning: NOAA GOES-19 Geostationary Lightning Mapper",
    "Water levels: NOAA Tides & Currents",
    "Fire: NASA FIRMS, National Interagency Fire Center",
    "Wildlife: observations from iNaturalist.org (CC BY-NC, observers credited per record)",
    "Flood zones: FEMA NFHL; levees: USACE National Levee Database",
    "WBGT: Liljegren et al. 2008 (Argonne National Laboratory method)",
]


def _print_table(pic, top):
    print(f"Hazard picture at {pic['generated_at']} ({pic['mode']} mode), {pic['site_count']} sites")
    for name, f in pic["feeds"].items():
        flag = "STALE" if f["stale"] else "ok"
        print(f"  feed {name:13s} {flag:5s} {f['fetched_at'] or '-':20s} {f['error'] or ''}")
    print()
    for e in pic["sites"][:top]:
        s = e["site"]
        wx = e["weather"] or {}
        wbgt = f"{wx['wbgt_c']:.1f}" if wx.get("wbgt_c") is not None else "-"
        band = e["band_pattern"] or ""
        print(f"{e['rank']:3d}. {s['name'][:32]:32s} {s['fuel']:8s} score {e['score']:5.1f}  "
              f"WBGT {wbgt:>5s}  {band}")
        for ev in e["events"][:4]:
            stale = " (stale)" if ev["stale"] else ""
            print(f"       - [{ev['severity']}] {ev['title']}{stale}")


def main():
    ap = argparse.ArgumentParser(description="Print the ranked site hazard picture")
    ap.add_argument("--fake", action="store_true", help="use the built-in demo scenario")
    ap.add_argument("--top", type=int, default=15, help="how many sites to show")
    ap.add_argument("--json", action="store_true", help="print the full JSON instead")
    args = ap.parse_args()
    if args.fake:
        store.set_mode("fake")
    pic = hazard_picture()
    if args.json:
        print(json.dumps(pic, indent=1))
    else:
        _print_table(pic, args.top)


if __name__ == "__main__":
    main()
