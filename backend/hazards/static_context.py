"""
Static flood context per site, looked up once (backend.sites.build_context)
and saved in backend/sites/site_context.json:

- FEMA flood zone (National Flood Hazard Layer, layer 28) at the site.
  VE = coastal high hazard, A/AE/AH/AO = 1% annual flood, shaded X = 0.2%,
  X = minimal. An empty answer means the map doesn't cover it, not "safe".
- USACE National Levee Database: is the site inside a leveed area (protected
  by a levee), and is that levee FEMA-accredited?

These never buzz a band. They add a small static risk bump and a line in
the 6 PM briefing, e.g. "behind non-accredited levee L-14".
"""

from functools import lru_cache

from backend.hazards.geo import distance_mi, point_in_geometry
from backend.hazards.http import get_json

FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
LEVEE_URL = "https://geospatial.sec.usace.army.mil/dls/rest/services/NLD/Public/FeatureServer/16/query"
LEVEE_NEARBY_MI = 3

# Flood zone -> (risk 0-3, plain words)
FEMA_RISK = {
    "VE": (3, "coastal high hazard (waves)"), "V": (3, "coastal high hazard (waves)"),
    "AE": (2, "1% annual chance flood"), "A": (2, "1% annual chance flood"),
    "AH": (2, "1% annual chance shallow flooding"), "AO": (2, "1% annual chance sheet flow"),
    "A99": (2, "1% annual chance, levee under construction"),
    "X_SHADED": (1, "0.2% annual chance flood"), "X": (0, "minimal flood hazard"),
    "D": (None, "undetermined"), "OPEN WATER": (None, "open water"),
}


def fema_risk(zone, subtype=""):
    """(risk 0-3 or None, description) for a FEMA zone."""
    if zone == "X" and subtype and "0.2" in subtype:
        return FEMA_RISK["X_SHADED"]
    return FEMA_RISK.get(zone, (None, "unknown zone"))


def lookup_fema_zone(site):
    """FEMA flood zone at the site."""
    d = get_json(FEMA_URL, {
        "geometry": f"{site.lon},{site.lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE", "returnGeometry": "false", "f": "json",
    }, timeout=40)
    if "error" in d:
        raise RuntimeError(d["error"])
    feats = d.get("features") or []
    if not feats:
        return {"zone": None, "risk": None, "label": "not mapped (unknown, not safe)"}
    # A site can straddle zones; keep the riskiest
    best = None
    for f in feats:
        a = f["attributes"]
        risk, label = fema_risk(a.get("FLD_ZONE"), a.get("ZONE_SUBTY") or "")
        if best is None or (risk or -1) > (best["risk"] or -1):
            bfe = a.get("STATIC_BFE")
            best = {"zone": a.get("FLD_ZONE"), "subtype": a.get("ZONE_SUBTY"), "risk": risk,
                    "label": label, "special_flood_hazard_area": a.get("SFHA_TF") == "T",
                    "base_flood_elevation_ft": bfe if bfe and bfe > -9000 else None}
    return best


@lru_cache(maxsize=1)
def _florida_leveed_areas():
    d = get_json(LEVEE_URL, {
        "where": "STATES LIKE '%Florida%'",
        "outFields": "SYSTEM_NAME,SYSTEM_ID,FEMA_ACCREDITATION_RATING,OVERTOPPING_ACE,FLOOD_SOURCES",
        "returnGeometry": "true", "maxAllowableOffset": 0.005, "outSR": 4326, "f": "geojson",
    }, timeout=60)
    return tuple(d.get("features") or [])


def _min_vertex_distance(site, geometry):
    rings = []
    if geometry["type"] == "Polygon":
        rings = geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        rings = [r for poly in geometry["coordinates"] for r in poly]
    return min((distance_mi(site.lat, site.lon, lat, lon) for ring in rings for lon, lat in ring),
               default=float("inf"))


def lookup_levee(site):
    """Leveed area the site is inside (or the nearest within 3 mi), or None."""
    best = None
    for f in _florida_leveed_areas():
        geom = f.get("geometry")
        if not geom:
            continue
        inside = point_in_geometry(site.lat, site.lon, geom)
        d = 0.0 if inside else _min_vertex_distance(site, geom)
        if d <= LEVEE_NEARBY_MI and (best is None or d < best["distance_mi"]):
            p = f["properties"]
            best = {"system": p.get("SYSTEM_NAME"), "system_id": p.get("SYSTEM_ID"),
                    "inside_leveed_area": inside, "distance_mi": round(d, 1),
                    "accredited": p.get("FEMA_ACCREDITATION_RATING") == "Accredited Levee System",
                    "accreditation": p.get("FEMA_ACCREDITATION_RATING"),
                    "overtopping_annual_chance": p.get("OVERTOPPING_ACE"),
                    "flood_sources": p.get("FLOOD_SOURCES")}
    return best


def static_flood_summary(ctx):
    """One line + a 0-3 static flood risk for the map and the briefing."""
    fema = ctx.get("fema") or {}
    levee = ctx.get("levees")
    risk = fema.get("risk") or 0
    parts = []
    if fema.get("zone"):
        parts.append(f"FEMA zone {fema['zone']} ({fema['label']})")
    elif "fema" in ctx:
        parts.append("FEMA: not mapped")
    if levee and levee["inside_leveed_area"]:
        kind = "accredited" if levee["accredited"] else "non-accredited"
        parts.append(f"behind {kind} levee {levee['system']}")
        if not levee["accredited"]:
            risk = max(risk, 1)
    return {"static_flood_risk": risk, "summary": "; ".join(parts) or None}
