"""
Look up static facts about each job site once and save them to
backend/sites/site_context.json (committed, so the demo works offline).

    python -m backend.sites.build_context            # fill in everything missing
    python -m backend.sites.build_context zones      # just one part
    python -m backend.sites.build_context gauges tides fema levees --redo

Parts:
    zones   NWS forecast zone, county and fire zone codes (to match alerts)
    gauges  nearest NWS river gauge (NWPS) with flood stages
    tides   nearest NOAA tide station with flood levels (coastal sites only)
    fema    FEMA flood zone at the site (NFHL layer 28)
    levees  nearest USACE National Levee Database levee area
"""

import json
import sys
import time
from pathlib import Path

from backend.sites.sites import load_sites

CONTEXT_PATH = Path(__file__).with_name("site_context.json")
PARTS = ("zones", "gauges", "tides", "fema", "levees")


def load_context():
    """The saved per-site facts: {site_id: {part: {...}}}. Empty if not built yet."""
    try:
        return json.loads(CONTEXT_PATH.read_text())
    except (OSError, ValueError):
        return {}


def save_context(ctx):
    CONTEXT_PATH.write_text(json.dumps(ctx, indent=1, sort_keys=True) + "\n")


def _lookup_fn(part):
    # Imported here so loading the context never needs the network modules.
    if part == "zones":
        from backend.hazards.nws import lookup_site_zones
        return lookup_site_zones
    if part == "gauges":
        from backend.hazards.flood import lookup_nearest_gauge
        return lookup_nearest_gauge
    if part == "tides":
        from backend.hazards.flood import lookup_nearest_tide_station
        return lookup_nearest_tide_station
    if part == "fema":
        from backend.hazards.static_context import lookup_fema_zone
        return lookup_fema_zone
    if part == "levees":
        from backend.hazards.static_context import lookup_levee
        return lookup_levee
    raise ValueError(part)


def build(parts=PARTS, redo=False, pause_s=0.3):
    ctx = load_context()
    for part in parts:
        fn = _lookup_fn(part)
        ok = failed = 0
        for site in load_sites():
            entry = ctx.setdefault(site.site_id, {})
            if part in entry and not redo:
                continue
            try:
                entry[part] = fn(site)
                ok += 1
            except Exception as e:          # keep going; one bad site shouldn't stop the rest
                failed += 1
                print(f"  {part} failed for {site.site_id} {site.name}: {e}")
            time.sleep(pause_s)             # be polite to free public APIs
        save_context(ctx)
        print(f"{part}: {ok} looked up, {failed} failed")
    return ctx


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    build(parts=args or PARTS, redo="--redo" in sys.argv)
