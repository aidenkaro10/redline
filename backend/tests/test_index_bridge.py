"""Session C2's hazard index flowing into C1's site picture and the frontend's SiteStatus."""

from backend.hazards import contract, index_bridge


def fake_index(site_id, index=6.3):
    return {"site_id": site_id, "date": index_bridge.tomorrow(), "index": index, "band": "extreme",
            "climatology_index": 2.0, "p_advisory": 0.9, "p_warning": 0.5, "p_damage": 0.1, "hazards": {},
            "top_reasons": [{"hazard": "heat", "feature": "hi_max_today", "value": 109, "points": 1.2,
                             "text": "Heat index reached 109°F by 6 PM today"}],
            "cutoff": "", "data_mode": "archive", "model_version": "test", "notes": []}


def test_index_for_only_returns_tomorrows_score(monkeypatch):
    monkeypatch.setattr(index_bridge, "_results", {"eia-663": fake_index("eia-663"),
                                                   "eia-1": {**fake_index("eia-1"), "date": "2000-01-01"}})
    assert index_bridge.index_for("eia-663")["index"] == 6.3
    assert index_bridge.index_for("eia-1") is None           # stale day
    assert index_bridge.index_for("nope") is None


def test_refresh_once_offline_first_then_online(monkeypatch):
    calls = []

    class S:
        def __init__(self, sid): self.site_id = sid

    def predict(site_id, date, offline=False, sites=None):
        calls.append((site_id, offline))
        if site_id == "b" and offline:
            raise FileNotFoundError("not cached")
        return fake_index(site_id)

    import ml.hazard_index.predict as p
    import ml.hazard_index.sites as s
    monkeypatch.setattr(p, "predict_index", predict)
    monkeypatch.setattr(s, "load_sites", lambda: [S("a"), S("b")])
    monkeypatch.setattr(index_bridge, "_results", {})
    assert index_bridge.refresh_once(online=False) == 1        # "b" isn't cached: skipped offline
    assert index_bridge.index_for("b") is None
    assert index_bridge.refresh_once(online=True) == 1         # now fetched online; "a" not redone
    assert index_bridge.index_for("b")["index"] == 6.3
    assert calls == [("a", True), ("b", True), ("b", True), ("b", False)]


def test_site_status_carries_full_index_and_reason_texts():
    entry = {"site": {"site_id": "eia-663"}, "rank": 1, "score": 1.0, "events": [], "band_pattern": None,
             "static": {"summary": ""}, "weather": None, "lightning": None, "flood": {}, "heat_risk": {},
             "hazard_index": fake_index("eia-663")}
    st = contract.site_status(entry, "2026-10-17T12:00:00Z")
    assert st["index_tomorrow"] == 6.3
    assert st["index_drivers"] == ["Heat index reached 109°F by 6 PM today"]
    assert st["hazard_index"]["band"] == "extreme"
    entry["hazard_index"] = None
    st = contract.site_status(entry, "2026-10-17T12:00:00Z")
    assert st["index_tomorrow"] is None and st["hazard_index"] is None
