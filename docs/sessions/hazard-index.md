# Session C2: hazard index, progress and results

Branch `claude/hazard-index-739442`. Code in `ml/hazard_index/`. How to call it: `docs/interfaces/hazard-index.md`.

## Status (Oct 17)

Done for this round:
- **Sites:** loader reads C1's `backend/sites/plants.geojson` when it lands, otherwise 12 placeholder sites (`ph-*`, approximate coordinates, spread across Florida). Everything below ran on the placeholders.
- **Labels, 2018-2024 (7 years, 30,684 site-days):** IEM warnings by point, IEM Local Storm Reports within 15 miles, NOAA Storm Events (deduped by episode, local standard time handled), OSHA heat injuries (codes 531, 5310, 5312) within 40 km. EH and XH both map to heat. Ladder: watch 1, advisory 3, warning 5, report 6, damage 8, injury 9, death 10.
- **Features known by 6 PM the day before:** heat index (persistence: today's max, since archived forecasts don't exist for 2018-2022), temperature, dew point, min humidity, gusts, rain 24 h / 72 h / 7 d, soil moisture (Open-Meteo archive), SPC Day 2 convective outlook, NWS products already in effect or issued by 6 PM, storm reports in the last 24 h, FEMA flood zone, levee, distance to river gauge, season. A leakage test scrambles everything after each day's cutoff and fails if any feature moves.
- **Models (scikit-learn, Aiden approved):** climatology by site and month, logistic regression, boosted trees (`HistGradientBoostingClassifier`), and the blend of the last two (shipped). Class weights for rare days, undone afterwards so probabilities stay honest.
- **predict_index works**, for past days (archive) and tomorrow (live). Plus `predict_all_sites` for the map and `past_incidents` for the briefing.

## Results (held-out 2023-2024)

PR-AUC: higher is better, random guessing scores the base rate. "blend − clim" has a 95% interval from resampling test weeks.

Main split: train 2018-2022, test 2023-2024, all 12 sites.

| hazard | level | test yes-days | base rate | climatology | logistic | trees | **blend** | blend − clim (95% CI) |
|---|---|---|---|---|---|---|---|---|
| overall | advisory+ | 1877 | 0.214 | 0.415 | 0.703 | 0.728 | **0.725** | +0.31 (+0.25, +0.37) |
| overall | warning+ | 1122 | 0.128 | 0.261 | 0.558 | 0.619 | **0.603** | +0.34 (+0.26, +0.42) |
| overall | damage+ | 204 | 0.023 | 0.057 | 0.126 | 0.140 | **0.134** | +0.08 (+0.03, +0.15) |
| heat | advisory+ | 556 | 0.063 | 0.187 | 0.688 | 0.613 | **0.679** | +0.49 (+0.35, +0.58) |
| heat | warning+ | 113 | 0.013 | 0.065 | 0.260 | 0.168 | **0.270** | +0.21 (+0.03, +0.34) |
| heat | injury | 53 | 0.006 | **0.036** | 0.019 | 0.041 | 0.029 | −0.01 (−0.03, +0.02) |
| storm | advisory+ | 732 | 0.083 | 0.188 | 0.378 | 0.432 | **0.415** | +0.23 (+0.16, +0.30) |
| storm | warning+ | 632 | 0.072 | 0.192 | 0.312 | 0.370 | **0.347** | +0.16 (+0.09, +0.22) |
| storm | damage+ | 123 | 0.014 | 0.049 | 0.119 | 0.169 | **0.139** | +0.09 (+0.04, +0.14) |
| flood | advisory+ | 914 | 0.104 | 0.212 | 0.551 | 0.594 | **0.587** | +0.38 (+0.31, +0.43) |
| flood | warning+ | 387 | 0.044 | 0.089 | 0.534 | 0.666 | **0.643** | +0.55 (+0.48, +0.61) |
| flood | damage+ | 25 | 0.003 | 0.005 | 0.035 | 0.032 | 0.035 | +0.03 (+0.01, +0.07) |
| tropical | advisory+ | 121 | 0.014 | 0.024 | 0.815 | 0.826 | **0.836** | +0.81 (+0.73, +0.89) |
| fire | advisory+ | 18 | 0.002 | 0.023 | 0.005 | 0.004 | 0.005 | too few days |

Calibration (expected calibration error, lower is better): on the advisory and warning rows the blend is better calibrated than climatology (e.g. overall advisory+ 0.037 vs 0.054, flood warning+ 0.004 vs 0.019). On the rare damage rows it's slightly worse (overall damage+ 0.009 vs 0.004).

Neighbor check (time + space): sites grouped with neighbors within 60 km, each group held out in turn (trained on the other groups 2018-2022, tested on the held-out group 2023-2024). The blend still wins everywhere it won above, e.g. overall warning+ 0.572 vs 0.205, heat advisory+ 0.672 vs 0.159, storm warning+ 0.302 vs 0.127. Full table: `python -m ml.hazard_index.evaluate`.

**Verdict: the blend beats climatology by PR-AUC on the held-out years for every hazard with enough test days.** Two exceptions ship climatology instead: fire (18 test days, no signal) and heat injury (OSHA reports, climatology wins).

## Flags: what looks too good, and why

- **Tropical 0.84 and flood warning+ 0.64 lean on NWS products already in effect at 6 PM.** That's legitimate (NWS had issued them by the briefing), but much of the lift is the model repeating NWS. Without any NWS or storm-report features: tropical 0.34, flood warning+ 0.19, overall warning+ 0.40, heat advisory+ 0.55, storm warning+ 0.34. Every hazard with signal still beats climatology without them. Say this out loud to judges: the index adds the most where NWS hasn't spoken yet (storms, heat).
- **Tropical has 121 test days but only a handful of storms** (Idalia 2023; Debby, Helene, Milton 2024). Big spread, as the interval shows.
- **Heat labels shift in 2023-2024:** heat advisories cover 5-6% of site-days vs about 1% before (record heat, plus NWS offices changed advisory criteria). Climatology from 2018-2022 can't see that. The logistic part helps here, since it keeps rising past the training range. That's why we ship the blend and not the trees alone.
- **County-wide matching over-labels.** A warning anywhere in the county counts for the site. Treat the labels as "NWS thought this area was at risk," not "it hit the plant."
- **The ship-climatology choice for fire and heat injury was made after seeing 2023-2024**, so those two rows are a little optimistic for the shipped mix.
- Fire weather: SPC fire outlooks can't be searched by point in IEM's history (only one day at a time), so they're left out. Red flag warnings and humidity carry fire.

## Gotchas found

- The python.org Python on this Mac can't check HTTPS certificates. `fetch.py` falls back to `curl`, which uses the Mac's store. Certificates are still checked.
- IEM sometimes has `issue` after `expire` on river flood warnings. We sort the two.
- IEM storm-report types differ from the docs (`RAIN`, `TROPICAL CYCLONE`). Mapped.
- OSHA uses 531, 5310, 5311 (indoor), 5312 (outdoor) for heat. We keep all but indoor. Florida has 631 heat injury reports since 2015.

## Next

1. When C1's `plants.geojson` lands: `python -m ml.hazard_index.labels`, then `features`, `evaluate`, `train` (about 3 s per site-year for IEM, 2-3 min per site for Open-Meteo; 50 sites ≈ 2.5 hours, and close to Open-Meteo's free daily limit, so split across two sittings).
2. Real day-ahead forecasts (Open-Meteo Previous Runs API, 2024 on) instead of persistence for heat. Would need a check that train and live inputs match.
3. Storm Events heat and tropical rows only match by zone code; zone shapes would place them better.
