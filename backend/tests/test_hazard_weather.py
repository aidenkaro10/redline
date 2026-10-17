"""Tests for the Open-Meteo weather feed + WBGT per hour. No internet."""

from datetime import timedelta

from backend.hazards import store, weather
from backend.hazards.events import iso, parse_time
from backend.hazards.testing import hazard_tmp_store  # noqa: F401  (autouse fixture)
from backend.sites.sites import get_site, load_sites

NOW = parse_time("2026-07-20T19:10:00Z")       # 3:10 PM in Florida
GNV = get_site("eia-663")


def hourly(temp=34.0, rh=60, solar=850, soil=0.15, rain=0.0):
    start = NOW.replace(minute=0) - timedelta(hours=24)
    times = [start + timedelta(hours=h) for h in range(72)]
    n = len(times)
    return {"time": [iso(t) for t in times], "temperature_2m": [temp] * n,
            "relative_humidity_2m": [rh] * n, "surface_pressure": [1013] * n,
            "wind_speed_10m": [2.5] * n, "shortwave_radiation": [solar] * n,
            "precipitation": [rain] * n, "soil_moisture_0_to_1cm": [soil] * n,
            "soil_moisture_1_to_3cm": [soil] * n, "soil_moisture_3_to_9cm": [soil] * n}


def test_hot_afternoon_gives_wbgt_heat_event(monkeypatch):
    monkeypatch.setattr(weather, "fetch_forecast", lambda sites: {GNV.site_id: hourly()})
    wx, result = weather.site_weather([GNV], now=NOW)
    now = wx[GNV.site_id]["now"]
    assert now["time"] == "2026-07-20T19:00:00Z"
    assert now["wbgt_flag"] in ("red", "black") and now["wbgt_c"] > 31
    events = weather.weather_events([GNV], wx, result)
    heat = [e for e in events if e.hazard == "heat"]
    assert heat and heat[0].severity in ("warning", "danger")


def test_wet_soil_and_heavy_rain(monkeypatch):
    # 3 mm every hour = 72 mm in 24 h, soil saturated
    monkeypatch.setattr(weather, "fetch_forecast",
                        lambda sites: {GNV.site_id: hourly(soil=0.40, rain=3.0, solar=200)})
    wx, result = weather.site_weather([GNV], now=NOW)
    kinds = {(e.hazard, e.severity) for e in weather.weather_events([GNV], wx, result)}
    assert ("soil", "warning") in kinds and ("flood", "advisory") in kinds


def test_weather_survives_wifi_drop(monkeypatch):
    monkeypatch.setattr(weather, "fetch_forecast", lambda sites: {GNV.site_id: hourly()})
    weather.site_weather([GNV], now=NOW)

    def offline(sites):
        raise OSError("WiFi down")
    monkeypatch.setattr(weather, "fetch_forecast", offline)
    wx, result = weather.site_weather([GNV], now=NOW + timedelta(hours=2))
    assert result.stale and wx[GNV.site_id]["now"]["wbgt_c"] is not None


def test_fake_mode_all_sites():
    store.set_mode("fake")
    sites = list(load_sites())
    wx, _ = weather.site_weather(sites, now=NOW)
    assert len(wx) == len(sites)
    assert wx["eia-645"]["now"]["wbgt_flag"] in ("red", "black")    # Tampa Bay hot spot
    assert wx["eia-136"]["now"]["rain_24h_mm"] > 50                  # night of heavy rain


def test_one_site_request_does_not_shrink_the_shared_cache(monkeypatch):
    monkeypatch.setattr(weather, "fetch_forecast", lambda sites: {s.site_id: hourly() for s in sites})
    weather.site_weather([GNV], now=NOW)                      # e.g. the planner asks for one site
    wx, _ = weather.site_weather(list(load_sites()), now=NOW)   # then the map asks for all
    assert len(wx) == len(load_sites())
