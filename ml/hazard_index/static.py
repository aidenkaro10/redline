"""
Site facts that don't change day to day: flood zone, levee, nearest river gauge.

Looked up once per site and cached (see fetch.py). Sources:
  - FEMA National Flood Hazard Layer, layer 28 (flood zone at a point)
  - USACE National Levee Database, layer 16 (leveed areas)
  - NWS National Water Prediction Service gauge list (nearest river gauge)
"""

from ml.hazard_index import fetch
from ml.hazard_index.sites import Site, distance_km

FEMA = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
NLD = "https://geospatial.sec.usace.army.mil/dls/rest/services/NLD/Public/FeatureServer/16/query"
NWPS_GAUGES = "https://api.water.noaa.gov/nwps/v1/gauges"
LEVEE_SEARCH_KM = 2          # "behind a levee" if a leveed area is this close

# FEMA zone -> flood risk score (from docs/data-sources.md: VE high, AE/A medium-high,
# shaded X low, plain X minimal). Unmapped is NOT safe, so it gets a middle value.
FLOOD_ZONE_SCORE = {"VE": 4, "V": 4, "AE": 3, "A": 3, "AH": 3, "AO": 3, "X_SHADED": 1, "X": 0, "D": 2}
UNMAPPED_SCORE = 2


def flood_zone(site: Site, offline: bool = False) -> tuple[str, int]:
    d = fetch.get_json(FEMA, dict(geometry=f"{site.lon},{site.lat}", geometryType="esriGeometryPoint",
                                  inSR=4326, spatialRel="esriSpatialRelIntersects",
                                  outFields="FLD_ZONE,ZONE_SUBTY,SFHA_TF", returnGeometry="false", f="json"),
                       offline=offline)
    feats = d.get("features", [])
    if not feats:
        return "unmapped", UNMAPPED_SCORE
    # a point can touch several polygons: keep the riskiest
    best = ("unmapped", -1)
    for f in feats:
        a = f["attributes"]
        zone = a.get("FLD_ZONE") or "unmapped"
        if zone == "X" and "0.2 PCT" in (a.get("ZONE_SUBTY") or ""):
            zone = "X_SHADED"                    # the 500-year floodplain
        score = FLOOD_ZONE_SCORE.get(zone, UNMAPPED_SCORE)
        if score > best[1]:
            best = (zone, score)
    return best


def levee(site: Site, offline: bool = False) -> tuple[int, str]:
    """(1, name) if a leveed area is within LEVEE_SEARCH_KM, else (0, '')."""
    d = fetch.get_json(NLD, dict(geometry=f"{site.lon},{site.lat}", geometryType="esriGeometryPoint",
                                 inSR=4326, spatialRel="esriSpatialRelIntersects",
                                 distance=LEVEE_SEARCH_KM, units="esriSRUnit_Kilometer",
                                 outFields="*", returnGeometry="false", f="json"),
                       offline=offline)
    feats = d.get("features", [])
    if not feats:
        return 0, ""
    a = feats[0]["attributes"]
    name = a.get("SYSTEM_NAME") or a.get("NAME") or a.get("LEVEED_AREA_NAME") or "levee"
    return 1, str(name)


def nearest_gauge(site: Site, offline: bool = False) -> tuple[str, float]:
    """(gauge id, km) for the closest NWS river/tide gauge in Florida."""
    d = fetch.get_json(NWPS_GAUGES, {"bbox.xmin": -88, "bbox.ymin": 24.3, "bbox.xmax": -79.8,
                                     "bbox.ymax": 31.1, "srid": "EPSG_4326"}, offline=offline)
    best = ("", 999.0)
    for g in d.get("gauges", []):
        if g.get("latitude") is None:
            continue
        km = distance_km(site.lat, site.lon, g["latitude"], g["longitude"])
        if km < best[1]:
            best = (g["lid"], round(km, 1))
    return best


def site_static(site: Site, offline: bool = False) -> dict:
    zone, zone_score = flood_zone(site, offline)
    behind_levee, levee_name = levee(site, offline)
    gauge, gauge_km = nearest_gauge(site, offline)
    return dict(flood_zone=zone, flood_zone_score=zone_score, levee=behind_levee,
                levee_name=levee_name, gauge=gauge, gauge_km=gauge_km)
