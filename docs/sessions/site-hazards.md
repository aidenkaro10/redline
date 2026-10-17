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
