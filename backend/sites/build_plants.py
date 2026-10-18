"""
Turn the EIA-860M spreadsheet into backend/sites/plants.geojson (our job sites).

Run once (the spreadsheet stays out of git, only the small GeoJSON is committed):
    python -m backend.sites.build_plants /path/to/august_generator2026.xlsx

What it does:
1. Reads the "Operating" sheet (header is on row 3).
2. Keeps Florida generators and groups them into plants by Plant ID
   (rows are generators, not plants).
3. Fixes bad coordinates: drops zeros, flips a positive longitude, and drops
   anything outside Florida.
4. Picks a mix of sites: every nuclear and coal plant, the biggest gas plants,
   the biggest solar farms, plus a few other kinds, spread around the state.

Data: U.S. Energy Information Administration, Form EIA-860M (public domain).
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from backend.sites.xlsx import read_rows

OUT_PATH = Path(__file__).with_name("plants.geojson")

# Rough box around Florida, used to catch bad coordinates
FL_LAT = (24.3, 31.1)
FL_LON = (-87.7, -79.8)

# How many of each kind to keep (nuclear and coal: keep all of them)
N_GAS = 32
N_SOLAR = 34
N_OTHER = 8

# Sites we always want, whatever their size (Gainesville is the default job site)
ALWAYS_KEEP_NAMES = ("Deerhaven", "J R Kelly")


def fuel_group(technology):
    """Collapse EIA's technology names into a few simple groups for the map."""
    t = (technology or "").lower()
    if "nuclear" in t:
        return "nuclear"
    if "natural gas" in t:
        return "gas"
    if "coal" in t:
        return "coal"
    if "solar" in t:
        return "solar"
    if "batter" in t:
        return "battery"
    if "petroleum" in t:
        return "oil"
    if "hydro" in t:
        return "hydro"
    return "biomass_waste"


def fix_coords(lat, lon):
    """Return a cleaned (lat, lon), or None if the point can't be trusted."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat == 0 or lon == 0:
        return None
    if lon > 0:                      # wrong sign: Florida is west of Greenwich
        lon = -lon
    if not (FL_LAT[0] <= lat <= FL_LAT[1] and FL_LON[0] <= lon <= FL_LON[1]):
        return None
    return round(lat, 5), round(lon, 5)


def load_florida_plants(xlsx_path):
    """Group Florida generators into plants. Returns a list of plant dicts."""
    rows = read_rows(xlsx_path, "Operating")
    header = None
    gens = defaultdict(list)
    for row in rows:
        if header is None:
            # The header row is the one that starts with "Entity ID"
            if row and row[0] == "Entity ID":
                header = {name: i for i, name in enumerate(row)}
            continue
        if len(row) <= header["Longitude"] or row[header["Plant State"]] != "FL":
            continue
        gens[int(row[header["Plant ID"]])].append(row)

    plants = []
    dropped = 0
    for plant_id, rows_ in gens.items():
        h = header
        coords = [c for c in (fix_coords(r[h["Latitude"]], r[h["Longitude"]]) for r in rows_) if c]
        if not coords:
            dropped += 1
            continue
        # Generators at one plant can disagree slightly; the median is robust.
        lat = statistics.median(c[0] for c in coords)
        lon = statistics.median(c[1] for c in coords)
        # Capacity per fuel group, so a plant gets the group with the most MW
        mw_by_group = defaultdict(float)
        techs = set()
        for r in rows_:
            mw = r[h["Nameplate Capacity (MW)"]]
            mw = float(mw) if isinstance(mw, float) else 0.0
            mw_by_group[fuel_group(r[h["Technology"]])] += mw
            techs.add(r[h["Technology"]])
        group = max(mw_by_group, key=mw_by_group.get)
        first = rows_[0]
        plants.append({
            "plant_id": plant_id,
            "name": first[h["Plant Name"]],
            "operator": first[h["Entity Name"]],
            "county": first[h["County"]],
            "lat": lat,
            "lon": lon,
            "fuel": group,
            "technologies": sorted(t for t in techs if t),
            "capacity_mw": round(sum(mw_by_group.values()), 1),
            "generators": len(rows_),
        })
    print(f"{len(plants)} Florida plants, {dropped} dropped for bad coordinates")
    return plants


def pick_sites(plants):
    """Choose a varied set of 50+ sites."""
    by_size = sorted(plants, key=lambda p: -p["capacity_mw"])
    chosen = {}

    def take(group, n=None):
        picked = [p for p in by_size if p["fuel"] == group and p["plant_id"] not in chosen]
        for p in picked[:n] if n else picked:
            chosen[p["plant_id"]] = p

    take("nuclear")
    take("coal")
    take("gas", N_GAS)
    take("solar", N_SOLAR)
    take("hydro")
    # A few others for variety (biomass, waste-to-energy, oil, batteries)
    others = [p for p in by_size if p["fuel"] in ("biomass_waste", "oil", "battery")
              and p["plant_id"] not in chosen]
    for p in others[:N_OTHER]:
        chosen[p["plant_id"]] = p
    for p in plants:
        if any(k.lower() in p["name"].lower() for k in ALWAYS_KEEP_NAMES):
            chosen[p["plant_id"]] = p
    return dedupe(sorted(chosen.values(), key=lambda p: -p["capacity_mw"]))


def dedupe(sites):
    """Drop a site that sits within ~300 m of a bigger one already kept.

    Some plants were rebuilt under a new Plant ID on the same land
    (Lauderdale and Dania Beach, for example), which would draw two pins
    on one spot.
    """
    kept = []
    for s in sites:              # biggest first, so the bigger one wins
        if all(abs(s["lat"] - k["lat"]) + abs(s["lon"] - k["lon"]) > 0.003 for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda p: p["plant_id"])


def to_geojson(sites):
    features = []
    for p in sites:
        props = {k: v for k, v in p.items() if k not in ("lat", "lon")}
        props["site_id"] = f"eia-{p['plant_id']}"
        props["lat"] = p["lat"]
        props["lon"] = p["lon"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": props,
        })
    return {
        "type": "FeatureCollection",
        "name": "Redline job sites: Florida power plants",
        "source": "U.S. Energy Information Administration, Form EIA-860M "
                  "(august_generator2026.xlsx, inventory of operating generators as of August 2026). "
                  "Public domain.",
        "features": features,
    }


def main(xlsx_path):
    plants = load_florida_plants(xlsx_path)
    sites = pick_sites(plants)
    OUT_PATH.write_text(json.dumps(to_geojson(sites), indent=1) + "\n")
    counts = defaultdict(int)
    for s in sites:
        counts[s["fuel"]] += 1
    print(f"Wrote {len(sites)} sites to {OUT_PATH}: {dict(counts)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m backend.sites.build_plants path/to/generator.xlsx")
    main(sys.argv[1])
