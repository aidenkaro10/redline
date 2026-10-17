import json

from ml.hazard_index import sites as S


def test_placeholder_loads_and_is_in_florida():
    sites = S.load_sites("placeholder")
    assert len(sites) >= 5
    assert any(s.site_id == "ph-gainesville" for s in sites)
    for s in sites:
        assert 24.0 < s.lat < 31.5 and -88.0 < s.lon < -79.5, s


def test_geojson_loader(tmp_path, monkeypatch):
    fake = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-82.3, 29.6]},
         "properties": {"plant_id": 123, "name": "Test Plant", "technology": "Solar"}}]}
    path = tmp_path / "plants.geojson"
    path.write_text(json.dumps(fake))
    monkeypatch.setattr(S, "GEOJSON_PATH", path)
    sites = S.load_sites("geojson")
    assert sites[0].site_id == "123" and sites[0].lat == 29.6 and sites[0].kind == "Solar"
    assert S.load_sites("auto")[0].site_id == "123"     # auto prefers the real file


def test_neighbor_groups_chain_and_split():
    a = S.Site("a", "a", 29.0, -82.0)
    b = S.Site("b", "b", 29.3, -82.0)     # ~33 km from a
    c = S.Site("c", "c", 29.6, -82.0)     # ~33 km from b, ~67 km from a: chained in
    d = S.Site("d", "d", 25.5, -80.3)     # far away
    g = S.neighbor_groups([a, b, c, d])
    assert g["a"] == g["b"] == g["c"] != g["d"]


def test_panhandle_timezone():
    assert S.Site("p", "p", 30.5, -87.2).timezone == "America/Chicago"
    assert S.Site("g", "g", 29.6, -82.3).timezone == "America/New_York"
