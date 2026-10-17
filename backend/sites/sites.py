"""
Load the job sites (Florida power plants) from plants.geojson.

    from backend.sites.sites import load_sites
    sites = load_sites()          # list of Site, 50+ of them
    sites[0].site_id, sites[0].lat, sites[0].lon

Source: U.S. Energy Information Administration, Form EIA-860M (public domain).
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PLANTS_PATH = Path(__file__).with_name("plants.geojson")


@dataclass(frozen=True)
class Site:
    site_id: str          # "eia-<Plant ID>", stable across runs
    name: str
    lat: float
    lon: float
    fuel: str             # gas, solar, nuclear, coal, oil, hydro, battery, biomass_waste
    capacity_mw: float
    county: str
    operator: str
    plant_id: int = 0
    technologies: tuple = field(default_factory=tuple)

    def to_dict(self):
        return {
            "site_id": self.site_id, "name": self.name, "lat": self.lat, "lon": self.lon,
            "fuel": self.fuel, "capacity_mw": self.capacity_mw, "county": self.county,
            "operator": self.operator, "plant_id": self.plant_id,
        }


@lru_cache(maxsize=1)
def load_sites(path=PLANTS_PATH):
    """Read every site from the GeoJSON. Cached, since the file never changes."""
    data = json.loads(Path(path).read_text())
    sites = []
    for f in data["features"]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        sites.append(Site(
            site_id=p["site_id"], name=p["name"], lat=lat, lon=lon, fuel=p["fuel"],
            capacity_mw=p["capacity_mw"], county=p["county"], operator=p["operator"],
            plant_id=p["plant_id"], technologies=tuple(p.get("technologies", ())),
        ))
    return tuple(sites)


def get_site(site_id):
    """Find one site by its id. Raises KeyError if there's no such site."""
    for s in load_sites():
        if s.site_id == site_id:
            return s
    raise KeyError(site_id)
