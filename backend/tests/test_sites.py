"""Tests for the job site list (plants.geojson) and the EIA converter helpers."""

from backend.hazards.geo import distance_mi, point_in_geometry
from backend.sites.build_plants import dedupe, fix_coords, fuel_group
from backend.sites.sites import get_site, load_sites


def test_at_least_50_florida_sites():
    sites = load_sites()
    assert len(sites) >= 50
    assert len({s.site_id for s in sites}) == len(sites)          # ids are unique
    for s in sites:
        assert 24.3 <= s.lat <= 31.1 and -87.7 <= s.lon <= -79.8   # inside Florida


def test_site_mix_has_gas_nuclear_and_solar():
    fuels = {s.fuel for s in load_sites()}
    assert {"gas", "nuclear", "solar"} <= fuels


def test_gainesville_site_present():
    deerhaven = get_site("eia-663")
    assert "Deerhaven" in deerhaven.name


def test_fix_coords():
    assert fix_coords(0, 0) is None                       # zeros are missing data
    assert fix_coords(29.7, 82.3) == (29.7, -82.3)        # wrong sign gets flipped
    assert fix_coords(40.0, -75.0) is None                # not in Florida
    assert fix_coords("", "") is None


def test_fuel_group():
    assert fuel_group("Natural Gas Fired Combined Cycle") == "gas"
    assert fuel_group("Solar Photovoltaic") == "solar"
    assert fuel_group("Nuclear") == "nuclear"


def test_dedupe_keeps_the_bigger_plant():
    a = {"plant_id": 1, "lat": 26.0686, "lon": -80.1984, "capacity_mw": 1447}
    b = {"plant_id": 2, "lat": 26.0686, "lon": -80.1984, "capacity_mw": 1215}
    assert [s["plant_id"] for s in dedupe([a, b])] == [1]


def test_distance_gainesville_to_jacksonville():
    # About 65-70 miles as the crow flies
    assert 60 < distance_mi(29.65, -82.32, 30.33, -81.66) < 75


def test_point_in_polygon():
    square = {"type": "Polygon", "coordinates": [[[-83, 29], [-82, 29], [-82, 30], [-83, 30], [-83, 29]]]}
    assert point_in_geometry(29.5, -82.5, square)
    assert not point_in_geometry(31.0, -82.5, square)
