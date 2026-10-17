# Data sources for the PlanIT Control track brief

Checked Oct 17, 2026, against each source's documentation, plus a few live API calls. This session's network blocks these sites for direct downloads, so nothing here was bulk-downloaded. Anything marked *not verified* should be tested in the first hours of the build.

## The short version

- **Most of the cited sources are historical or static.** Only NOAA Tides & Currents is live. Storm Events runs 4 to 5 months behind, OSHA about 10 months, and FEMA, the levee database and EIA-860M are maps or lists.
- **So the brief's live alerts need sources it doesn't cite.** All of them are free: GOES-19 satellite lightning, NASA FIRMS fire detections, Open-Meteo for weather, heat inputs and soil moisture, NWS river gauges and alerts, and iNaturalist for wildlife.
- **The cited sources are the right ones for training the 0-10 hazard index.** The best label source is the IEM warnings archive, which returns every NWS warning that covered a point between two dates. Storm Events and OSHA add events that actually caused harm.
- **OSHA ties directly to your twist.** Its records carry codes for heat exposure and for falls, so the same data that labels site hazards also shows these are the injuries that put crews in hospital.

## Which source covers which requirement

| Brief requirement | Live source | Static or historical source |
| --- | --- | --- |
| 50+ real power plant sites | none needed | EIA-860M, converted once to a GeoJSON |
| Weather, extreme heat | NWS alerts, Open-Meteo, NWS HeatRisk | Open-Meteo archive, IEM ASOS |
| Lightning, 10-mile stop-work with 30-min countdown | GOES-19 GLM (Xweather as a cross-check) | Storm Events (damage cases only) |
| Flooding after a night of rain | NWS river gauges (NWPS), NWS flood alerts, Tides & Currents for coastal sites | FEMA flood zone, levee database, IEM warnings |
| Wildfire | NASA FIRMS, NIFC current incidents, Red Flag Warnings | Storm Events, IEM fire weather warnings |
| Soil saturation | Open-Meteo soil moisture | Open-Meteo archive |
| Wildlife sightings | iNaturalist, plus your own crew reports | GBIF |
| 0-10 ML hazard index | the live features above | IEM warnings, Storm Events, OSHA, Open-Meteo archive |
| Daily 6 PM briefing | forecasts only (Open-Meteo, NWS, HeatRisk, SPC outlooks via IEM) | past incidents near the site from OSHA and Storm Events |

## The seven sources the brief cites

### EIA-860M: the job sites

- **What it is:** a monthly spreadsheet of every US generator of 1 MW or more, with plant ID, name, utility, state, county, latitude, longitude, technology and capacity. It's public domain; credit "Source: U.S. Energy Information Administration".
- **Get it:** [landing page](https://www.eia.gov/electricity/data/eia860m/). The newest file is [august_generator2026.xlsx](https://www.eia.gov/electricity/data/eia860m/xls/august_generator2026.xlsx), and the next release is Oct 23.
- **Use it:** filter to Florida, group generators to plants by Plant ID, pick 50+ sites, and save a static `plants.geojson`. Then the demo needs no key and nothing can break. Mix in gas and nuclear plants so the list isn't all solar farms. FPL has roughly 150 plants, about 127 of them solar (third-party count, *not verified* in the file).
- **Gotchas:** rows are generators, not plants. Some coordinates are zero or have the wrong sign. There may be header rows above the column names. The EIA API needs a free key and may not return coordinates, and the old ArcGIS power plant layers now require a login, so don't build on them.

### NOAA Storm Events: historical hazard outcomes

- **What it is:** NWS records of weather events that caused damage, injury or death. All 48 event types are recorded from 1996.
- **Get it:** yearly CSVs at [csvfiles](https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/) (details, locations and fatalities files; details is about 13 MB gzipped per year). There's no API, and file names change when NCEI refreshes them, so read the directory listing. Data currently runs through May 2026.
- **Useful columns:** EVENT_TYPE, BEGIN_DATE_TIME with CZ_TIMEZONE (local standard time), STATE, CZ_TYPE and CZ_FIPS, BEGIN_LAT and BEGIN_LON, injuries, deaths, damage, and narratives. The fatalities file has location codes that fit crews, such as heavy equipment and outside.
- **Gotchas:** lightning appears only when it caused harm, so it's not a strike record. Heat, wildfire, hurricane and storm surge are recorded by forecast zone without coordinates, so you need zone shapes to place them. Damage values are strings like "10.00K". One storm creates many rows, so deduplicate by EPISODE_ID. It has no wildlife data.

### Iowa Environmental Mesonet: the NWS warnings archive

- **What it is:** Iowa State's archive of NWS watches, warnings and advisories, Local Storm Reports, SPC outlooks and ASOS weather observations. It's public domain, with attribution appreciated. It publishes no rate limits but asks heavy-traffic sites not to call it live.
- **The key endpoint** returns every warning that covered a point between two dates. A live test for Gainesville, June to September 2024, returned 75 events (severe thunderstorm, tornado, flash flood, flood, heat advisory, tropical storm and others):
  `https://mesonet.agron.iastate.edu/json/vtec_events_bypoint.py?lat=29.65&lon=-82.32&sdate=2024-06-01&edate=2024-09-30`
  It matches by county or zone. `sbw_by_point.py` with the same parameters matches the exact storm-warning polygon instead.
- **Also useful:** Local Storm Reports by point and radius (`/api/1/nws/lsrs_by_point.json`, tested: 43 reports within 25 miles of Gainesville that summer). These include reports with no damage and arrive sooner than Storm Events. The [IEM API](https://mesonet.agron.iastate.edu/api/1/docs) also has SPC convective and fire outlooks by point, flash-flood guidance and drought category. [ASOS hourly observations](https://mesonet.agron.iastate.edu/request/download.phtml) give heat index, rain, gusts and thunder codes.
- **Gotchas:** NWS replaced the Excessive Heat warning code (EH) with Extreme Heat (XH) in March 2025, so map both. IEM has no lightning or wildlife data.

### OSHA Severe Injury Reports: real worker harm

- **What it is:** employer reports of amputations, hospitalizations and eye losses since Jan 1, 2015, with address, latitude and longitude, NAICS industry code, event date, a narrative and injury codes.
- **Get it:** [dashboard](https://www.osha.gov/severe-injury-reports). The current file is [January2015toNovember2025.zip](https://www.osha.gov/sites/default/files/January2015toNovember2025.zip), about 103,000 rows. The file name changes with each update.
- **Heat and falls:** event code 531 is exposure to environmental heat, 43x is a fall to a lower level and 42x is a fall on the same level. Exposure to electricity is 511x and 512x. Also search the narratives for words like ladder, bucket and pole. OSHA counted about 296 heat hospitalizations in Florida from 2015 to 2021. Industry codes to weight most: 2211 (utilities), 237130 (power line construction) and 238210 (electrical contractors).
- **Gotchas:** it only records injuries, so for training you compare each injury day with ordinary days at the same place. Florida's state and local government workers aren't covered, so city utilities like GRU, JEA and OUC won't appear. Reports are badly undercounted, so treat counts as a floor. Few records sit exactly at a power plant, so match by industry and distance.

### National Levee Database: static context only

- **What it is:** USACE's map of levees and floodwalls, with a [public feature service](https://geospatial.sec.usace.army.mil/dls/rest/services/NLD/Public/FeatureServer) that returns GeoJSON by point or box. A test point near Lake Okeechobee returned the levee area "L-14".
- **Florida:** 85 levee systems, mostly Everglades canal levees. The Herbert Hoover Dike isn't in it; it's in the National Inventory of Dams.
- **Useful fields:** system name, flood sources, FEMA accreditation, annual chance of overtopping, and levee height. There's no risk rating or inspection condition.
- **Verdict:** a small static risk bump and a briefing line such as "behind non-accredited levee L-14". The Boreholes layer is useless for soil saturation, since it only has 12 Florida points and no moisture data. Spend an hour at most.

### NOAA Tides & Currents: live coastal water levels

- **What it is:** the only live cited source. It gives water levels every 6 minutes plus tide predictions, with no key. A live test at Mayport returned a reading timestamped Oct 17, 10:12.
- **Get it:** the [data API](https://api.tidesandcurrents.noaa.gov/api/prod/datagetter) (`station`, `product=water_level` or `predictions`, `datum`, `units`, `time_zone`, `format=json`, `date=latest`). Per request it returns up to 1 month of 6-minute data or 1 year of hourly data.
- **Flood thresholds:** `/mdapi/prod/webapi/stations/{id}/floodlevels.json` gives minor, moderate and major flood levels. They're relative to the station's own datum, so request water levels with `datum=STND` and compare directly.
- **Gotchas:** coastal sites only. There's no search by coordinates, so download the [station list](https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=waterlevels) once and pick the nearest station yourself. Values come back as strings marked preliminary.

### FEMA flood maps: each site's flood zone

- **What it is:** the [Map Service Center](https://msc.fema.gov/portal) and the National Flood Hazard Layer. Layer 28 of the [NFHL map service](https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer) returns the flood zone at a point:
  `.../MapServer/28/query?geometry={lon},{lat}&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects&outFields=FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE&returnGeometry=false&f=json`
- **Use it:** as a static score. VE is high, AE and A are medium-high, shaded X is low and plain X is minimal. Look up all sites once and cache the results.
- **Gotchas:** the live query is *not verified* because it was blocked from here, so test it first. The maps are often years old. An empty result means unmapped, not safe; check layer 0 (NFHL availability).

## Sources the brief doesn't cite but the live alerts need

- **Lightning: GOES-19 Geostationary Lightning Mapper.** Free, on AWS with no key (`noaa-goes19` bucket, product `GLM-L2-LCFA`). It writes a NetCDF file every 20 s with flash latitude, longitude and time, so a strike-to-alert time of 30 to 60 s is realistic, with about 4 km location error. Use GOES-19, not GOES-16, which NOAA no longer sends. **Caveat:** NOAA reported extra false flashes from GOES-19's lightning sensor since July 2026, worst over the tropics in the afternoon, with no fix announced. Keep only flashes with `flash_quality_flag == 0` and require 2 or more before stopping work.
  - Xweather (Vaisala) has a free tier, but it only allows about 1,500 lightning calls a month, enough to cross-check a few demo sites but not to poll all 50.
  - Blitzortung is ruled out: its terms forbid using it for storm warnings.
  - NWS Severe Thunderstorm Warnings can't drive the 10-mile rule, because they only cover severe storms.
- **Wildfire: [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/).** Free MAP_KEY by email. One Florida bounding-box call every 10 minutes is plenty (VIIRS and MODIS sources, CSV output). Satellites only pass a few times a day. Add [NIFC current incident locations](https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query) (no key, GeoJSON, refreshed every 5 minutes) and NWS Red Flag Warnings.
- **Soil saturation and weather: [Open-Meteo](https://open-meteo.com/).** No key. Soil moisture at five depths, temperature, humidity, solar radiation and wind, for several sites per request and up to 16 days ahead. Compute WBGT from it, since the NWS hourly forecast lacks solar radiation. The free tier is non-commercial with CC BY 4.0 data and up to 10,000 calls a day. A student demo is fine; if Sperry wants to adopt it, they'd need a paid plan.
- **Extreme heat: NWS HeatRisk** (0 to 4, 7 days, hourly updates), readable per point from an [ArcGIS image service](https://mapservices.weather.noaa.gov/experimental/rest/services/NWS_HeatRisk/ImageServer). It's still experimental, so treat it as a display label.
- **River flooding: [NWS National Water Prediction Service](https://api.water.noaa.gov/nwps/v1/gauges/ASTF1).** No key. Each gauge gives flood stages, the current reading and a forecast. A live test of the St. Johns River at Astor showed a minor flood now and a moderate flood forecast for Oct 20. This is the source for "flood warning after a night of heavy rain." The old USGS stream gauge service is being retired, and its replacement needs a free key beyond a few calls an hour.
- **Active alerts: `https://api.weather.gov/alerts/active?point={lat},{lon}`** (no key, User-Agent header required). The `point` and `event` filters are *not verified*.
- **Wildlife: [iNaturalist](https://api.inaturalist.org/v1/docs/).** Observations by point and radius or bounding box, with no key for reading. Look up taxon IDs at runtime: American alligator, pit vipers, coral snakes and red imported fire ants. Stay under 60 requests a minute, credit observers, and stay non-commercial (CC BY-NC). Sightings are sporadic, so your crew's own photo reports will carry most of this. There is no public Florida FWC nuisance-alligator feed.
- **Historical weather for training: the [Open-Meteo archive](https://open-meteo.com/en/docs/historical-weather-api)** (ERA5 from 1940, about 5 days behind, no key). 50 sites over 5 years is about 6,500 calls, so spread the download over a day.

## Training the 0-10 hazard index

- **One row per site per day.** Features come only from what is known by 6 PM the evening before: forecast heat index or WBGT, rain over the last 24 and 72 hours, soil moisture, SPC convective and fire outlooks, flood zone, levee status, distance to a river gauge, and season.
- **Labels per hazard:** whether a warning covered the site the next day (from IEM), and whether a Storm Event or Local Storm Report happened within a set radius. Weight them into 0 to 10: advisory low, warning medium, damage or injury high, death 10. Use OSHA heat and fall cases to boost the heat and outdoor-work components.
- **Pitfalls:**
  - Most site-days are zero, so use class weights and judge by PR-AUC, not accuracy.
  - Split by time (for example, train through 2022 and test on 2023 to 2024), and keep neighboring sites out of both sides.
  - A warning doesn't mean the hazard happened, and county-wide matching over-labels.
  - Storm Events times are local standard time, while IEM times are UTC.

## How your wristband twist fits

- **Site score and worker score are separate layers.** The 0-10 index says how dangerous a site's conditions are tomorrow. The band says how a specific worker is coping right now. The supervisor map can rank sites by the index and flag sites where a worker's estimated core temperature or a man-down alert is active.
- **OSHA backs up both features.** Heat exposure and falls are coded in the same file, so you can show judges how many utility and line-construction workers were hospitalized for each.
- **Your current team doc covers the band and NWS alerts at one site (Gainesville).** The brief's 50+ sites, risk-ranked map, 6 PM briefing, crew photo reports and 0-10 index aren't in it yet.

## Test these early (not verified)

- FEMA NFHL layer 28 point query.
- NWS alerts `point` and `event` filters.
- Whether the EIA API returns coordinates (the spreadsheet route avoids the question).
- iNaturalist taxon IDs.
- Whether FIRMS returns its fastest (ultra-real-time) detections through the API.
- Exact EIA-860M sheet names and OSHA column names; open each file once and print the headers.
