"""Small geography helpers: distance between two points, nearest thing."""

import math

EARTH_RADIUS_MI = 3958.8


def distance_mi(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def nearest(lat, lon, items, key=lambda x: (x["lat"], x["lon"])):
    """Return (item, distance in miles) for the item closest to (lat, lon)."""
    best, best_d = None, float("inf")
    for it in items:
        ilat, ilon = key(it)
        d = distance_mi(lat, lon, ilat, ilon)
        if d < best_d:
            best, best_d = it, d
    return best, best_d


def point_in_polygon(lat, lon, ring):
    """True if (lat, lon) is inside a polygon ring of [lon, lat] pairs (GeoJSON order)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lat, lon, geometry):
    """True if the point is inside a GeoJSON Polygon or MultiPolygon (outer rings only)."""
    if not geometry:
        return False
    kind = geometry.get("type")
    if kind == "Polygon":
        return point_in_polygon(lat, lon, geometry["coordinates"][0])
    if kind == "MultiPolygon":
        return any(point_in_polygon(lat, lon, poly[0]) for poly in geometry["coordinates"])
    return False
