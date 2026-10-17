# Session C2: hazard index, progress and results

Branch `claude/hazard-index-739442`. Code in `ml/hazard_index/`. How to call it: `docs/interfaces/hazard-index.md`.

## Status (Oct 17)

Done, on C1's 79 real Florida power plants (`backend/sites/plants.geojson`, ids `eia-<plant id>`):
- **Labels, 2018-2024 (7 years, 202,003 site-days):** IEM warnings by point, IEM Local Storm Reports within 15 miles, NOAA Storm Events (deduped by episode, local standard time handled), OSHA heat injuries (codes 531, 5310, 5312) within 40 km. EH and XH both map to heat. Ladder: watch 1, advisory 3, warning 5, report 6, damage 8, injury 9, death 10.
- **Features known by 6 PM the day before:** heat index (persistence: today's max, since archived forecasts don't exist for 2018-2022), temperature, dew point, min humidity, gusts, rain 24 h / 72 h / 7 d, soil moisture (Open-Meteo archive), SPC Day 2 convective outlook, NWS products already in effect or issued by 6 PM, storm reports in the last 24 h, FEMA flood zone, levee, distance to river gauge, season. A leakage test scrambles everything after each day's cutoff and fails if any feature moves.
- **Models (scikit-learn, Aiden approved):** climatology by site and month, logistic regression, boosted trees (`HistGradientBoostingClassifier`), and the blend of the last two (shipped). Class weights for rare days, undone afterwards so probabilities stay honest.
- **Ship rule:** the model is used for a question only if its PR-AUC gain over climatology on 2023-2024 is positive AND its 95% interval stays above zero. Otherwise climatology ships (the model file lists these under `climatology_only`): fire (all levels), heat injury, flood damage.
- **predict_index works**, for past days (archive) and tomorrow (live). Plus `predict_all_sites` for the map and `past_incidents` for the briefing.
- Weather download: 70 weather cells (sites within about 10 km share one), 25 s apart for Open-Meteo's free limits; took about 3.5 hours. Merged with `main` (setting renamed `HAZARD_INDEX_DATA_DIR`).

## Results (held-out 2023-2024, 79 sites)

PR-AUC: higher is better, random guessing scores the base rate. "blend − clim" has a 95% interval from resampling test weeks. Train 2018-2022, test 2023-2024, all 79 sites.

| hazard | level | test yes-days | base rate | climatology | logistic | trees | **blend** | blend − clim (95% CI) |
|---|---|---|---|---|---|---|---|---|
| overall | advisory+ | 12,226 | 0.212 | 0.410 | 0.745 | 0.770 | **0.768** | +0.36 (+0.31, +0.40) |
| overall | warning+ | 8,044 | 0.139 | 0.291 | 0.672 | 0.711 | **0.705** | +0.41 (+0.35, +0.47) |
| overall | damage+ | 1,070 | 0.019 | 0.046 | 0.106 | 0.116 | **0.116** | +0.07 (+0.03, +0.13) |
| heat | advisory+ | 3,402 | 0.059 | 0.182 | 0.703 | 0.621 | **0.703** | +0.52 (+0.37, +0.61) |
| heat | warning+ | 730 | 0.013 | 0.050 | 0.308 | 0.130 | **0.302** | +0.25 (+0.01, +0.42) |
| heat | injury | 289 | 0.005 | **0.028** | 0.015 | 0.025 | 0.020 | −0.01 (−0.03, +0.00) |
| storm | advisory+ | 3,998 | 0.069 | 0.155 | 0.377 | 0.397 | **0.395** | +0.24 (+0.18, +0.31) |
| storm | warning+ | 3,409 | 0.059 | 0.155 | 0.341 | 0.339 | **0.350** | +0.20 (+0.14, +0.26) |
| storm | damage+ | 647 | 0.011 | 0.037 | 0.085 | 0.099 | **0.090** | +0.05 (+0.02, +0.09) |
| flood | advisory+ | 6,287 | 0.109 | 0.269 | 0.670 | 0.719 | **0.710** | +0.44 (+0.37, +0.49) |
| flood | warning+ | 3,872 | 0.067 | 0.239 | 0.755 | 0.814 | **0.798** | +0.56 (+0.49, +0.61) |
| flood | damage+ | 128 | 0.002 | 0.010 | 0.077 | 0.066 | 0.080 | +0.07 (−0.00, +0.23) → climatology ships |
| tropical | advisory+ | 794 | 0.014 | 0.022 | 0.781 | 0.845 | **0.849** | +0.83 (+0.77, +0.90) |
| tropical | damage+ | 38 | 0.001 | 0.001 | 0.053 | 0.085 | **0.059** | +0.06 (+0.03, +0.15) |
| fire | any | 115 | 0.002 | 0.004 | 0.005 | 0.006 | 0.005 | +0.00 (−0.00, +0.01) → climatology ships |

Calibration (expected calibration error, lower is better): on every advisory and warning row the blend is as well or better calibrated than climatology (e.g. overall advisory+ 0.030 vs 0.052, flood warning+ 0.005 vs 0.020). On the rare damage rows it is a hair worse (overall damage+ 0.007 vs 0.003).

**Neighbor check (time + space):** Florida split into 4 blocks of nearby sites. Each block is held out in turn; training uses only sites more than 60 km from every test site (45 to 77 sites per block), tested on 2023-2024. The blend still wins everywhere above, e.g. overall warning+ 0.696 vs 0.184, heat advisory+ 0.677 vs 0.178, storm warning+ 0.342 vs 0.081, flood warning+ 0.776 vs 0.105. Full table: `python -m ml.hazard_index.evaluate`.

**Verdict: the blend beats climatology on held-out years by PR-AUC for every hazard and level where the gain is clear, including on places it never saw.** Three questions ship climatology because the gain was not clear: fire, heat injury and flood damage.

For comparison, the first run on 12 placeholder sites gave the same picture (overall warning+ 0.603 vs 0.261).

## Flags: what looks too good, and why

- **Flood, tropical and the overall score lean on NWS products already in effect at 6 PM.** That's legitimate (NWS had issued them by the briefing), but a lot of the lift is the model repeating NWS. Without any NWS or storm-report features, on 79 sites: overall advisory+ 0.504 (vs climatology 0.410), overall warning+ 0.373 (vs 0.291), heat advisory+ 0.601 (vs 0.182), storm advisory+ 0.369 (vs 0.155), tropical 0.306 (vs 0.022). **But flood falls to climatology level** (advisory+ 0.265 vs 0.269, warning+ 0.211 vs 0.239). Say this out loud to judges: for flooding the model mostly knows NWS has already spoken; it adds the most for heat and storms, where NWS hasn't.
- **Tropical has 794 test days but only a handful of storms** (Idalia 2023; Debby, Helene, Milton 2024). Big spread.
- **Heat labels shift in 2023-2024:** heat advisories cover 5-6% of site-days vs about 1% before (record heat, plus NWS offices changed advisory criteria). Climatology from 2018-2022 can't see that. The logistic part helps here since it keeps rising past the training range, which is why we ship the blend and not the trees alone (trees alone score 0.13 on heat warning+, the blend 0.30).
- **County-wide matching over-labels.** A warning anywhere in the county counts for the site. Treat the labels as "NWS thought this area was at risk," not "it hit the plant."
- **The ship-climatology choice was made after seeing 2023-2024**, so those rows are a little optimistic for the shipped mix.
- Fire weather: SPC fire outlooks can't be searched by point in IEM's history (only one day at a time), so they're left out. Fire is too rare here (0.4% of days) to learn from.

## Gotchas found

- The python.org Python on this Mac can't check HTTPS certificates. `fetch.py` falls back to `curl`, which uses the Mac's store. Certificates are still checked.
- IEM sometimes has `issue` after `expire` on river flood warnings. We sort the two.
- IEM storm-report types differ from the docs (`RAIN`, `TROPICAL CYCLONE`). Mapped.
- Open-Meteo sometimes returns an empty reply under load: `get_json` now waits and retries.
- OSHA uses 531, 5310, 5311 (indoor), 5312 (outdoor) for heat. We keep all but indoor. Florida has 631 heat injury reports since 2015.

## Next

1. Real day-ahead forecasts (Open-Meteo Previous Runs API, 2024 on) instead of persistence for heat. Needs a check that train and live inputs match.
2. Storm Events heat and tropical rows only match by zone code; zone shapes would place them better.
3. When more sites or years are added: run the four commands in `docs/interfaces/hazard-index.md`. Weather is the slow part (about 3 minutes per weather cell).
