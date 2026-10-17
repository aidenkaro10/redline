"""Session C1 bridge, with stand-ins for C1's modules (their code lives on another branch)."""

from types import SimpleNamespace

from backend.alerts.band_output import MultiBand
from backend.app.hub import Hub
from backend.app.schemas import Reading
from backend.app.site_bridge import SiteBridge


def make(pic):
    hub = Hub(band=MultiBand([]))
    b = SiteBridge.__new__(SiteBridge)          # skip the real import of C1's code
    b.hub, b.ours, b.crew_site = hub, {}, "eia-663"
    b.contract = SimpleNamespace(
        site_records=lambda: [{"id": "eia-663", "name": "Deerhaven", "lat": 29.76, "lon": -82.39, "tech": "gas"},
                              {"id": "eia-628", "name": "Crystal River", "lat": 28.96, "lon": -82.70}],
        site_statuses=lambda p: [{"site_id": e["site"]["site_id"], "hazards": [], "index_tomorrow": 3}
                                 for e in p["sites"]])
    b.picture = SimpleNamespace(latest_picture=lambda: pic["now"],
                                band_commands=lambda p: {e["site"]["site_id"]: f"BUZZ:{e['band_pattern']}"
                                                         for e in p["sites"] if e["band_pattern"]})
    return hub, b


def test_sites_default_and_band_patterns():
    pic = {"now": {"generated_at": "x", "sites": [{"site": {"site_id": "eia-663"}, "band_pattern": "LIGHTNING"},
                                                   {"site": {"site_id": "eia-628"}, "band_pattern": "HEAT_YELLOW"}]}}
    hub, b = make(pic)
    assert b.load_sites() == 2 and hub.default_site_id == "eia-663" and "site-gnv" not in hub.sites
    hub.handle_input(Reading(worker_id="w1", timestamp=1.0, hr=90))
    assert hub.workers["w1"]["site_id"] == "eia-663"
    b.refresh()
    assert hub.pipelines["w1"].router.current == "LIGHTNING"
    assert hub.site_status["eia-663"]["index_tomorrow"] == 3
    assert "eia-628" not in hub.hazards or not hub.hazards["eia-628"]      # site heat never buzzes bands
    pic["now"] = {"generated_at": "y", "sites": [{"site": {"site_id": "eia-663"}, "band_pattern": None}]}
    b.refresh()
    assert hub.pipelines["w1"].router.current == "OFF"


def test_no_picture_yet_is_fine():
    hub, b = make({"now": None})
    b.load_sites()
    b.refresh()
