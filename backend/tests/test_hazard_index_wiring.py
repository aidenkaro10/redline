"""Tests for wiring C2's hazard index into the hazard picture. No model, no internet."""

from backend.hazards import contract, picture, store
from backend.hazards import index as hidx
from backend.hazards.events import parse_time
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site, load_sites

AM = parse_time("2026-07-20T15:00:00Z")      # 11 AM Florida
PM = parse_time("2026-07-20T23:00:00Z")      # 7 PM Florida
SITES = [get_site("eia-663"), get_site("eia-645")]


def fake_predict(site_id, day, offline=False):
    return {"site_id": site_id, "date": day.isoformat(), "index": 4.2 if site_id == "eia-645" else 1.1,
            "band": "high", "climatology_index": 2.0, "p_advisory": 0.5, "p_warning": 0.2,
            "p_damage": 0.02, "hazards": {"heat": {"index": 3.0}, "storm": {"index": 1.0}},
            "top_reasons": [{"hazard": "heat", "feature": "hi", "value": 109, "points": 1.2,
                             "text": "Heat index reached 109°F by 6 PM today"}],
            "data_mode": "live", "model_version": "t1", "notes": []}


def reset(monkeypatch=None):
    hidx.reset()


def test_tomorrow_and_cache_key_switch_at_6pm_local():
    assert hidx.tomorrow(AM).isoformat() == "2026-07-21"
    assert hidx.cache_key(AM) == "2026-07-21-am" and hidx.cache_key(PM) == "2026-07-21-pm"


def test_real_path_computes_in_background_then_caches_once(monkeypatch):
    hidx.reset()
    calls = []

    def predict(site_id, day, offline=False):
        calls.append(site_id)
        return fake_predict(site_id, day)
    got, status = hidx.hazard_indexes(SITES, AM, predict=predict)
    hidx.wait_for_worker(10)
    got, status = hidx.hazard_indexes(SITES, AM, predict=predict)
    assert status["state"] == "ready" and status["done"] == 2 and status["model_version"] == "t1"
    assert got["eia-645"]["index"] == 4.2
    hidx.hazard_indexes(SITES, AM, predict=predict)                 # cached: no second run
    assert sorted(calls) == ["eia-645", "eia-663"]
    # A fresh process reads the saved file instead of recomputing
    hidx.reset()
    got, status = hidx.hazard_indexes(SITES, AM, predict=lambda *a, **k: 1 / 0)
    assert status["state"] == "ready" and got["eia-663"]["index"] == 1.1
    # After 6 PM it is a new key, so it recomputes once more
    hidx.hazard_indexes(SITES, PM, predict=predict)
    hidx.wait_for_worker(10)
    assert len(calls) == 4
    hidx.reset()


def test_missing_model_gives_none_not_a_crash():
    hidx.reset()

    def no_model(site_id, day, offline=False):
        raise FileNotFoundError("hazard_index.joblib")
    hidx.hazard_indexes(SITES, AM, predict=no_model)
    hidx.wait_for_worker(10)
    got, status = hidx.hazard_indexes(SITES, AM, predict=no_model)
    assert got == {"eia-663": None, "eia-645": None}
    assert status["state"] == "unavailable" and "FileNotFoundError" in status["error"]
    hidx.reset()


def test_one_failing_site_keeps_the_others():
    hidx.reset()

    def flaky(site_id, day, offline=False):
        if site_id == "eia-663":
            raise OSError("no internet")
        return fake_predict(site_id, day)
    hidx.hazard_indexes(SITES, AM, predict=flaky)
    hidx.wait_for_worker(10)
    got, status = hidx.hazard_indexes(SITES, AM, predict=flaky)
    assert got["eia-663"] is None and got["eia-645"]["index"] == 4.2 and status["state"] == "partial"
    hidx.reset()


def test_fake_mode_values_are_believable_and_stable():
    store.set_mode("fake")
    sites = list(load_sites())
    a, status = hidx.hazard_indexes(sites, AM)
    b, _ = hidx.hazard_indexes(sites, AM)
    assert a == b and status["state"] == "fake" and len(a) == len(sites)
    for r in a.values():
        assert 0 <= r["index"] <= 10 and r["data_mode"] == "mock" and r["fake"]
        assert set(r["hazards"]) == set(hidx.HAZARD_KINDS)
        assert r["band"] == hidx.band_for(r["index"])
    assert a["eia-645"]["top_reasons"][0]["hazard"] == "heat"        # Tampa Bay heat demo site
    assert max(a.values(), key=lambda r: r["index"])["index"] > 3.0
    assert len({round(r["index"], 1) for r in a.values()}) > 10      # not all the same


def test_picture_fills_index_ranks_riskiest_tomorrow_and_feeds_the_contract():
    store.set_mode("fake")
    pic = picture.hazard_picture(now=AM)
    assert pic["index_status"]["state"] == "fake"
    assert all(e["hazard_index"] for e in pic["sites"])
    risky = pic["riskiest_tomorrow"]
    assert [r["index"] for r in risky] == sorted((r["index"] for r in risky), reverse=True)
    by_id = {e["site"]["site_id"]: e for e in pic["sites"]}
    assert by_id[risky[0]["site_id"]]["tomorrow_rank"] == 1
    assert sorted(e["tomorrow_rank"] for e in pic["sites"]) == list(range(1, len(pic["sites"]) + 1))
    st = contract.site_status(by_id["eia-645"], pic["generated_at"])
    assert st["index_tomorrow"] == by_id["eia-645"]["hazard_index"]["index"]
    assert st["index_drivers"] == [r["text"] for r in by_id["eia-645"]["hazard_index"]["top_reasons"]]
    assert st["hazard_index"]["hazards"]["heat"]["index"] > 0


def test_picture_without_the_model_has_none_everywhere(monkeypatch):
    # Real mode, no model: every feed would need the internet, so stub the index only
    store.set_mode("fake")
    monkeypatch.setattr(hidx, "hazard_indexes", lambda sites, now=None, predict=None: (
        {s.site_id: None for s in sites},
        {"date": "2026-07-21", "state": "unavailable", "done": 0, "total": len(sites),
         "computed_at": None, "model_version": None, "error": "FileNotFoundError: model"}))
    pic = picture.hazard_picture(now=AM)
    assert pic["riskiest_tomorrow"] == [] and all(e["hazard_index"] is None for e in pic["sites"])
    assert all(e["tomorrow_rank"] is None for e in pic["sites"])
    st = contract.site_status(pic["sites"][0], pic["generated_at"])
    assert st["index_tomorrow"] is None and st["index_drivers"] == [] and st["hazard_index"] is None


def test_offline_pass_first_then_downloads_only_what_is_missing():
    hidx.reset()
    calls = []

    def predict(site_id, day, offline=False):
        calls.append((site_id, offline))
        if site_id == "eia-645" and offline:
            raise FileNotFoundError("not downloaded yet")
        return fake_predict(site_id, day)
    hidx.hazard_indexes(SITES, AM, predict=predict)
    hidx.wait_for_worker(10)
    assert calls == [("eia-663", True), ("eia-645", True), ("eia-645", False)]
    got, status = hidx.hazard_indexes(SITES, AM, predict=predict)
    assert status["state"] == "ready" and got["eia-645"]["index"] == 4.2
    hidx.reset()


def test_hazard_index_setting_offline_and_off(monkeypatch):
    hidx.reset()
    calls = []

    def predict(site_id, day, offline=False):
        calls.append((site_id, offline))
        raise FileNotFoundError("not downloaded yet")
    monkeypatch.setenv("HAZARD_INDEX", "offline")
    hidx.hazard_indexes(SITES, AM, predict=predict)
    hidx.wait_for_worker(10)
    assert all(off for _, off in calls)                      # never went online
    got, status = hidx.hazard_indexes(SITES, AM, predict=predict)
    assert got == {"eia-663": None, "eia-645": None} and status["state"] == "unavailable"
    hidx.reset()
    calls.clear()
    monkeypatch.setenv("HAZARD_INDEX", "off")
    got, status = hidx.hazard_indexes(SITES, AM, predict=predict)
    assert calls == [] and status["error"] == "HAZARD_INDEX=off" and set(got.values()) == {None}
