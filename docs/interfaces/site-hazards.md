# Site hazard layer: interface (session C1)

Code: `backend/sites/` (job sites) and `backend/hazards/` (live feeds, WBGT, rules). Progress notes and endpoint tests: [docs/sessions/site-hazards.md](../sessions/site-hazards.md).

## Quick start

```bash
python -m backend.hazards.picture --fake        # demo scenario, no internet
python -m backend.hazards.picture --top 20      # live APIs
python -m backend.hazards.picture --json        # the full JSON
```

Settings in `.env` (all optional, see `.env.example`):

| Key | Default | What |
| --- | --- | --- |
| `HAZARD_MODE` | `live` | `fake` = built-in demo data for every feed, no internet |
| `NWS_USER_AGENT` | placeholder | NWS asks for an app name + contact email |
| `FIRMS_MAP_KEY` | empty | NASA FIRMS fire detections. Without it, FIRMS is skipped (feed says so) |
| `HAZARD_DATA_DIR` | `data/hazards` | caches + report log (has its own `.gitignore`) |

Safety constants (10 mi, 30 min, 2 flashes, WBGT flags, flood distances...) are in the **SITE HAZARD LAYER** block at the end of `backend/config.py`.

## Job sites

- `backend/sites/plants.geojson`: 79 real Florida power plants from EIA-860M (32 gas, 34 solar, 2 nuclear, 2 coal, 3 oil, 4 biomass/waste, 1 hydro, 1 battery). Credit: "U.S. Energy Information Administration, Form EIA-860M".
- Site ids are `eia-<EIA Plant ID>`, e.g. **`eia-663` = Deerhaven Generating Station, Gainesville** (the default crew site; the frontend mock calls it `fl-deerhaven`).
- `backend/sites/site_context.json`: per site, looked up once: NWS zones, nearest river gauge + flood stages, nearest tide station + flood levels, FEMA flood zone, levee.

```python
from backend.sites.sites import load_sites, get_site
sites = load_sites()            # tuple of Site(site_id, name, lat, lon, fuel, capacity_mw, county, operator, plant_id)
get_site("eia-663").to_dict()
```

Rebuild (only if the plant list changes): `python -m backend.sites.build_plants path/to/generator.xlsx`, then `python -m backend.sites.build_context`.

## The one call: `hazard_picture()`

```python
from backend.hazards.picture import hazard_picture, latest_picture, start_background_refresh, band_commands
start_background_refresh(interval_s=60)   # at server start (first build takes ~10-15 s: lightning backfill)
pic = latest_picture()                    # instant, no network; None until the first build finishes
band_commands(pic)                        # {"eia-628": "BUZZ:LIGHTNING", ...}
```

Returns:

```text
{
  "generated_at": ISO UTC, "mode": "live"|"fake", "site_count": 79,
  "event_counts": {"info": n, "advisory": n, "warning": n, "danger": n},
  "feeds": {name: {"fetched_at", "stale", "error"}},   # nws_alerts, nws_heatrisk, open_meteo, goes19_glm,
                                                         # nwps_gauges, tides, firms, nifc, inaturalist
  "sites": [ SiteEntry, ... ]   # ranked, worst first
  "credits": [ ... ]            # show these somewhere on the dashboard
}
```

**SiteEntry**: `rank`, `score`, `site` (site dict), `top_severity`, `band_pattern` (LIGHTNING > FLOOD_FIRE > HEAT_YELLOW, or null), `events` (worst first), `stale`, `weather` (current hour: temp_c, rh_pct, wind_ms_10m, solar_wm2, wbgt_c, globe_c, wbgt_flag, heat_index_f, soil_moisture_m3m3, rain_24h_mm, wbgt_max_next_12h / _today / _tomorrow), `heat_risk` (today/tomorrow: value 0-4, label, color), `lightning` (rule state, below), `flood` (river gauge + tide readings), `static` (FEMA zone, levee, one-line summary), `tomorrow` (wbgt_max, heat_risk, river_forecast, alerts_into_tomorrow), `hazard_index` (null until C2 plugs in).

**HazardEvent** (every hazard from every feed has this shape):

```json
{
 "event_id": "eia-628|lightning|GOES-19 GLM|stop-2026-07-20T18:05:20Z",
 "site_id": "eia-628", "hazard": "lightning", "severity": "danger", "level": 4,
 "title": "Lightning within 10 mi (5.2 mi): STOP WORK, all-clear in 30 min",
 "source": "GOES-19 GLM", "source_id": "stop-2026-07-20T18:05:20Z",
 "status": "active", "starts": "2026-07-20T18:05:20Z", "ends": "2026-07-20T18:40:00Z",
 "observed_at": "2026-07-20T18:10:00Z", "fetched_at": "2026-07-20T18:10:00Z",
 "stale": false, "fake": true, "band_pattern": "LIGHTNING",
 "details": {"state": "stop_work", "minutes_to_all_clear": 30.0, "nearest_mi_30min": 5.2, "...": "..."}
}
```

- `hazard`: heat, lightning, flood, fire, soil, wildlife, weather (other NWS alerts: wind, tornado, tropical).
- `severity`: info < advisory < warning < danger (`level` 1-4).
- `status`: active, or `all_clear_pending` for a lightning stop that's counting down.
- `stale: true` means the feed is down and this is the last good copy. Show it greyed with its `fetched_at`.
- `band_pattern` is a suggestion; the alert router (A) decides what the band actually gets.

Which hazards buzz (`band_pattern`): lightning stop-work → LIGHTNING; flood or fire **warning/danger** (NWS alert, river in flood, wildfire close by) → FLOOD_FIRE; tornado/hurricane warning → FLOOD_FIRE; site heat warning (Heat Advisory, WBGT red/black flag) → HEAT_YELLOW. Forecasts, soil, wildlife and HeatRisk never buzz.

### Lightning state (`entry["lightning"]`)

`state` (clear | nearby | unconfirmed | stop_work), `stop_started_at`, `last_strike_at`, `all_clear_at`, `minutes_to_all_clear`, `all_clear_unconfirmed` (true if the lightning feed was down when the countdown ran out: we keep the stop), `nearest_mi_30min`, `flashes_10mi_30min`, `flashes_30mi_30min`, `strikes` (newest 50 within 30 mi: lat, lon, at, miles).

## For session A (live system)

- Start `start_background_refresh(60)` on server start; serve `latest_picture()`.
- Frontend shapes are ready in `backend/hazards/contract.py`:
  - `site_records()` → `GET /api/sites` (`id, name, operator, tech, capacity_mw, lat, lon, county, coastal`).
  - `site_statuses(pic)` → one `SiteStatus` per site for `site_status` events, exactly as in the frontend contract: six hazards in order heat, lightning, flood, fire, soil, wildlife; levels none|low|moderate|high|extreme; Unix-second times; lightning `nearest_miles, last_strike_at, all_clear_at, strikes[]`; heat `wbgt_c, heat_index_f`. Extra fields: `rank`, `score`, `other_alerts` (wind/tornado/tropical), `static_flood`, `stale`/`fake` per hazard. Pass `worker_flag` and `workers_on_site` yourself.
  - `test_alert("LIGHTNING" | "FLOOD_FIRE", site_id=None)` → `POST /api/demo/test-alert` (default site `eia-663`). Lightning drops 2 fake strikes 3 mi away, so the real rule and 30-min countdown run.
  - `clear_test_hazards()` → `POST /api/demo/clear-hazards`.
- Live/fake switch for a demo control: `backend.hazards.store.set_mode("fake" | "live")`.
- Any hazard by hand: `backend.hazards.inject.inject_test_event(site_id, hazard, severity="warning", minutes=30, title=None)`.
- Lightning stop-work should count as a break in the schedule (frontend contract says so).
- Times: hazard feeds are real-world (wall clock) data. During replay, the picture still shows the real current hazards unless you switch to fake mode.

## For session B (planner, 6 PM briefing)

- Per site: `entry["tomorrow"]` (max WBGT tomorrow with time and flag, HeatRisk tomorrow, river forecast, NWS alerts running into tomorrow) plus `entry["static"]["summary"]` (e.g. "FEMA zone AO; behind non-accredited levee L-8 West, L-10 and L-12").
- Hourly forecast with WBGT for hazard windows and heat budgets (from 24 h ago to 3 days ahead):

```python
from backend.hazards.weather import site_weather
from backend.sites.sites import get_site
wx, feed = site_weather([get_site("eia-663")])
h = wx["eia-663"]["hourly"]     # lists: time (ISO UTC), temperature_2m, relative_humidity_2m, wind_speed_10m,
                                # shortwave_radiation, precipitation, soil_moisture_*, wbgt, globe_temp, wbgt_flag
```

- Lightning stop-work windows: `entry["lightning"]["stop_started_at"]` to `["all_clear_at"]`.

## For session C2 (hazard index ML)

- Site list: `load_sites()` or read `backend/sites/plants.geojson` directly (properties: `site_id, plant_id, name, lat, lon, fuel, capacity_mw, county, operator`).
- Static features per site in `backend/sites/site_context.json`: `fema.zone / risk (0-3)`, `levees.inside_leveed_area / accredited`, `gauges.distance_mi` and flood stages, `tides` (coastal or not), `zones` (NWS UGC codes, useful to match IEM warnings by zone).
- WBGT, vectorized (numpy arrays in, arrays out; about 0.7 s per 100,000 rows):

```python
from backend.hazards.wbgt import wbgt_liljegren, wbgt_from_weather, cos_zenith, wind_at_2m, wbgt_flag
out = wbgt_liljegren(tair_c, rh_pct, pres_hpa, wind_ms_2m, solar_wm2, cza)   # dict: wbgt, tg, tnwb, tpsy, fdir
# One weather row (Open-Meteo style: 10 m wind, previous-hour solar):
wbgt_from_weather(time_utc, lat, lon, tair_c, rh_pct, pres_hpa, wind_ms_10m, solar_wm2)
```

  For Open-Meteo archive data, compute `cza` at the middle of the previous hour (`time - 30 min`) and bring the 10 m wind down with `wind_at_2m(speed, 10.0, cza > 0, solar)`, exactly like `backend/hazards/weather.py:add_wbgt`.
- When the index exists, fill `entry["hazard_index"] = {"index": 0-10, "drivers": [...]}`; `contract.site_status` already maps it to `index_tomorrow` / `index_drivers`. Tell me the function to call and I'll wire it into `hazard_picture()`.

## For session E (frontend)

- Use the `SiteStatus` from `contract.site_statuses()` (via A's `site_status` events). Site ids are `eia-...`, not `fl-...`; Deerhaven is `eia-663`.
- Map rings: 10 mi stop radius and 30 mi watch radius (`LIGHTNING_STOP_RADIUS_MI`, `LIGHTNING_WATCH_RADIUS_MI`). Count down to `all_clear_at`; it moves later with every new strike.
- Show `stale` hazards greyed with "last updated ...", and `fake` ones with a TEST/DEMO badge.
- Show `pic["credits"]` (EIA, NOAA/NWS, Open-Meteo CC BY 4.0, iNaturalist CC BY-NC, FEMA, USACE) on the map page.

## Report log (every hazard reported, with source and time)

`data/hazards/hazard_log.jsonl`, append-only, one JSON line per change: `{"logged_at", "kind": "reported" | "updated" | "cleared" | "injected", "event": HazardEvent}`. Feeds that are down never mark their events cleared.

```python
from backend.hazards.report_log import get_log
get_log().read(site_id="eia-663", since="2026-10-17T00:00:00Z")
```
