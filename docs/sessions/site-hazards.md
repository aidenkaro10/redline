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
