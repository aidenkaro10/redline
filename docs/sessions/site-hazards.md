# Session C1: site hazard layer (progress notes)

Owns `backend/sites/` and `backend/hazards/`. Interface for other sessions: [docs/interfaces/site-hazards.md](../interfaces/site-hazards.md).

## Endpoint tests (live, Oct 17, 2026)

| Source | What I tested | Result |
| --- | --- | --- |
| EIA-860M | `august_generator2026.xlsx` (14 MB, approved by Aiden) | Sheet "Operating", header on row 3, columns `Plant ID`, `Plant State`, `Latitude`, `Longitude`, `Technology`, `Nameplate Capacity (MW)`. 751 FL generators -> 346 plants. No bad coordinates in FL this month, but the cleaner still drops zeros, flips positive longitudes and drops points outside Florida. Lauderdale and Dania Beach share one spot (rebuilt under a new Plant ID), so the converter de-duplicates pins within ~300 m. |
| NWS alerts | `alerts/active?area=FL` | Works, one call for the whole state (11 alerts today). Most alerts have no polygon, only UGC zone codes, so each site's zones are looked up once. |
| NWS alerts | `point=` and `event=` filters (were *not verified*) | Both work: `?point=29.65,-82.32` and `?area=FL&event=Flood%20Warning`. We use `area=FL` + zone matching instead of 79 point calls. |
| NWS points | `/points/{lat},{lon}` | Gives forecastZone, county and fireWeatherZone URLs; codes saved in `backend/sites/site_context.json` for all 79 sites. |
| NWS HeatRisk | ImageServer `getSamples` with a multipoint | One call returns all 7 days for all sites (`idp_validtime` per day, value 0-4). |

## Gotchas found

- **SSL on this Mac:** the venv's python.org Python has no certificate bundle, so HTTPS failed with `CERTIFICATE_VERIFY_FAILED`. `backend/hazards/http.py` uses `certifi` (already installed with httpx).
- **Open-Meteo:** works with all 79 sites in one call (~1.6 s), `wind_speed_unit=ms`, `timezone=GMT`, `past_days=1`, `forecast_days=3`. Soil moisture around Gainesville on a dry October day is ~0.13 m³/m³; the wet/saturated thresholds (0.30 / 0.38) are a first guess for Florida sand, check them after a real rain event. Solar radiation is the mean over the previous hour, so WBGT uses the sun angle at mid-hour.

## WBGT (Liljegren)

- `backend/hazards/wbgt.py` is a line-by-line numpy port of Liljegren's `wbgt.c` (Argonne v1.1, read from github.com/mdljts/wbgt; constants copied from `wbgt.h`). Vectorized, so C2 can run years of hourly data at once.
- No published table of Liljegren outputs was available as data (the HeatStress R package's reference set is an R binary and we have no R), so the tests check against published formulas instead: Stull 2011's worked example (20 °C, 50% -> 13.7 °C; ours 13.63), the WMO aspirated-psychrometer equation (within 0.45 °C from 20 to 35 °C), Buck's saturation vapor pressure, sun position at the solstice, and physical behavior (sun raises WBGT, wind cools the globe, no-sun globe ≈ air temp).
- Florida noon check: 33 °C, 55% RH, 2 m/s, 850 W/m² -> WBGT 32.1 °C (black flag), globe 48 °C.

## Lightning (GOES-19 GLM)

- Bucket listing works over plain HTTPS, no boto3: `https://noaa-goes19.s3.amazonaws.com/?list-type=2&prefix=GLM-L2-LCFA/2026/290/11/`. Files land ~17 s after their 20 s window ends, ~250 KB each (~45 MB an hour if we keep up). netCDF4 (approved) applies `scale_factor`/`add_offset` itself; flash times are offsets from the `units` string.
- First live run: 103 files (35 min backfill) in 10.7 s, then incremental. 135 Florida-box flashes, 133 with `flash_quality_flag == 0`, nearest 40 mi from any site (offshore storms).
- Rule: 2 good flashes within 10 mi within 30 min -> stop work; all-clear 30 min after the last flash within 10 mi, reset by every new one.
- **Safety choice I added (tell Aiden):** if the GLM feed has had no fresh data for 5 min (`LIGHTNING_FEED_STALE_MIN`), the all-clear is never given, because we can't know there were no new strikes. The site stays in stop-work with "can't confirm all-clear".
- The first call takes ~10 s (backfill). Session A should run `refresh_all()` in a background task, not inside a request.

## Flooding

- **NWPS:** `https://api.water.noaa.gov/nwps/v1/gauges?bbox.xmin=-88&bbox.ymin=24.3&bbox.xmax=-79.8&bbox.ymax=31.1&srid=EPSG_4326` returns all 847 gauges in and around Florida in one call, each with current and forecast `floodCategory`. So the live loop is one call; per-gauge detail (`/gauges/{lid}`, flood stages) is only fetched once per site. 72 of 79 sites have a gauge with flood stages within 15 mi (49 gauges). Astor (ASTF1) live: minor now (2.51 ft), moderate forecast Oct 20, matching data-sources.md.
- **Tides & Currents:** station list has 302 water-level stations; 15 sites are within 12 mi of one (12 stations). Flood levels come as `nws_minor/moderate/major` and `nos_*`, in feet on the station datum; `water_level&datum=STND&date=latest` is directly comparable (Mayport 11.0 ft vs minor 15.8 ft today). We use the NWS levels, NOS as fallback. Values come back as strings marked preliminary.
- River forecasts are capped at "advisory" so a flood forecast for tomorrow shows on the map and briefing but never buzzes a band. Observed minor flood -> warning (FLOOD_FIRE), moderate/major -> danger.

## Wildfire, wildlife, static context

- **NIFC** current incidents: `where=POOState='US-FL'` works, 3 Florida incidents today (one prescribed burn, type `RX`). Prescribed or 100% contained -> info; active wildfire within 10 mi -> warning.
- **FIRMS:** not tested live yet, no `FIRMS_MAP_KEY` (Aiden gets it by email). Without a key the feed is skipped and reports "FIRMS_MAP_KEY not set". Still open: whether the API returns the ultra-real-time detections.
- **iNaturalist** taxon IDs (verified): alligator 26159, pit vipers (Crotalinae) 797576, coral snakes (Micrurus) 30493, red imported fire ant 67598. One paged Florida query for the last 7 days: 225 sightings today (116 alligators, 31 cottonmouths, 15 eastern diamondbacks). Many observations have no license ("all rights reserved"), so we only show counts, distance and a link back, never photos.
- **FEMA NFHL layer 28** point query (was *not verified*): works, but the server resets connections under load; re-run `build_context fema` for any gaps (it skips sites already done). Zones across our 79 sites: 52 X, 8 AE, 7 A, 1 AO, 1 D, 1 open water, 9 filled on retry.
- **Levees:** "Leveed Areas" is layer 16 of the NLD FeatureServer; the point+distance query returned nothing, so I pull all 85 Florida leveed areas once (80 KB simplified) and test each site locally. 3 sites sit inside a leveed area (Okeelanta, West County, Dania Beach), 4 more within 3 mi.
