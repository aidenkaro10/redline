"""
The job sites the hazard index scores.

Two sources, picked by the HAZARD_SITES setting (or the `source` argument):
  "auto"         (default) use backend/sites/plants.geojson if it exists, else the placeholder
  "geojson"      always use backend/sites/plants.geojson (session C1 builds it from EIA-860M)
  "placeholder"  the small hand-made list below

PLACEHOLDER WARNING: the coordinates below are approximate (typed from public maps),
just good enough to pull weather and warnings while C1 finishes the real file.
Do not show them on the demo map as real plant locations.
"""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

GEOJSON_PATH = Path(__file__).resolve().parents[2] / "backend" / "sites" / "plants.geojson"

# Sites closer than this are "neighbors". Neighbors share warnings (NWS warns by
# county), so they must land on the same side of the train/test split.
NEIGHBOR_KM = 60.0


@dataclass(frozen=True)
class Site:
    site_id: str
    name: str
    lat: float
    lon: float
    kind: str = ""          # gas, nuclear, solar, coal, ...

    @property
    def timezone(self) -> str:
        """Florida's panhandle west of the Apalachicola River runs on Central time."""
        return "America/Chicago" if (self.lon < -85.0 and self.lat > 29.6) else "America/New_York"


# (site_id, name, lat, lon, kind). PLACEHOLDER, approximate coordinates.
PLACEHOLDER_SITES = [
    ("ph-gainesville", "Gainesville (Deerhaven area), placeholder", 29.758, -82.388, "gas"),
    ("ph-crystal-river", "Crystal River area, placeholder", 28.958, -82.700, "coal"),
    ("ph-jacksonville", "Jacksonville Northside area, placeholder", 30.425, -81.553, "gas"),
    ("ph-orlando", "Orlando Stanton area, placeholder", 28.484, -81.168, "coal"),
    ("ph-tampa", "Tampa Big Bend area, placeholder", 27.795, -82.403, "gas"),
    ("ph-manatee", "Manatee area, placeholder", 27.605, -82.345, "gas"),
    ("ph-fort-myers", "Fort Myers area, placeholder", 26.697, -81.783, "gas"),
    ("ph-st-lucie", "St. Lucie area, placeholder", 27.348, -80.246, "nuclear"),
    ("ph-martin", "Martin County area, placeholder", 27.053, -80.563, "solar"),
    ("ph-turkey-point", "Turkey Point area, placeholder", 25.435, -80.331, "nuclear"),
    ("ph-pensacola", "Pensacola Crist area, placeholder", 30.566, -87.226, "gas"),
    ("ph-tallahassee", "Tallahassee area, placeholder", 30.380, -84.300, "gas"),
]


def _from_geojson(path: Path) -> list[Site]:
    """Read C1's plants.geojson. Accepts a few spellings of the id field."""
    data = json.loads(path.read_text())
    sites = []
    for feat in data["features"]:
        p = feat.get("properties", {})
        lon, lat = feat["geometry"]["coordinates"][:2]
        sid = p.get("site_id") or p.get("id") or p.get("plant_id") or p.get("eia_plant_id")
        kind = p.get("kind") or p.get("technology") or p.get("fuel") or ""
        sites.append(Site(str(sid), p.get("name", str(sid)), float(lat), float(lon), str(kind)))
    return sites


def load_sites(source: str | None = None) -> list[Site]:
    """The list of sites to build labels, features and predictions for."""
    source = source or os.getenv("HAZARD_SITES", "auto")
    if source == "geojson" or (source == "auto" and GEOJSON_PATH.exists()):
        return _from_geojson(GEOJSON_PATH)
    if source in ("placeholder", "auto"):
        return [Site(*row) for row in PLACEHOLDER_SITES]
    raise ValueError(f"unknown site source {source!r}; use auto, geojson or placeholder")


def get_site(site_id: str, sites: list[Site] | None = None) -> Site:
    for s in sites or load_sites():
        if s.site_id == site_id:
            return s
    raise KeyError(f"no site with id {site_id!r}")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine formula)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def neighbor_groups(sites: list[Site], km: float = NEIGHBOR_KM) -> dict[str, int]:
    """Give each site a group number. Sites chained within `km` of each other share one."""
    group: dict[str, int] = {}
    for start in sites:
        if start.site_id in group:
            continue
        # flood-fill: everything reachable through neighbor links joins this group
        g = len(set(group.values()))
        todo = [start]
        group[start.site_id] = g
        while todo:
            a = todo.pop()
            for b in sites:
                if b.site_id not in group and distance_km(a.lat, a.lon, b.lat, b.lon) < km:
                    group[b.site_id] = g
                    todo.append(b)
    return group
