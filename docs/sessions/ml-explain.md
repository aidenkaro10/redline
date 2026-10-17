# Session H: explainability, Weaver weights, result figures

Branch: `claude/ml-explain-071c08`. Owns: `ml/explain/`, `ml/figures/`, `docs/figures/`, `docs/interfaces/ml-explain.md`, this file. Nothing in `backend/models/` or other `ml/` files was changed. Not merged, not pushed.

Everything is scored on Mendeley **validation** people only. Test people were never touched.

## How to run (from the repo root, with the project Python)

1. Tests: `python -m pytest ml/explain ml/figures -q`  (13 pass)
2. Why-lines on val examples: `python -m ml.explain.examples`
3. Weaver vs equal weights: `python -m ml.explain.weaver`
4. Figures (rewrites the 5 PNGs in `docs/figures/`): `python -m ml.figures.make_figures`

Data and checkpoints are the gitignored ones in the main checkout, referenced by absolute path (`MAIN` in each script). Use `PYTHONPATH=.` if Python can't find `backend`.
One test to verify: step 1. `test_attributions_add_up_on_the_real_ensemble` checks the shares add up to the forecast difference.

## 1. Explainability (`ml/explain/explain.py`)

`explain(signals, minute, predictor) -> {"why", "direction", "contributions", ...}`. Interface for the live system: [docs/interfaces/ml-explain.md](../interfaces/ml-explain.md). Takes about 0.1 to 0.3 s per call.

How: integrated gradients (32 steps) from a calm reference worker (65 bpm, WBGT 20 °C, light work, just started, same sensor gaps) to the real inputs, averaged over the 5 ensemble members. Explains how far the 20-min 90th percentile sits above the current estimate. Known-future weather and planned work are included, so "it's 31 °C WBGT" can be a reason. Columns are pooled into: heart_rate, body_heat, skin_temp, activity, wbgt, workload, time.

Design choices to know:
- I explain "p90 forecast minus now", not the raw p90. Reason: with the raw number, "you are already hot" swamps everything. Subtracting now cancels the Kalman estimate, so the shares say what drives the change.
- Direction word comes from the median forecast vs now (±0.15 °C). If steady, the line says where the estimate sits and names the biggest upward pressure, so it still reads sensibly when a yellow alert fires on the 90th percentile.

Sanity tests (all pass): shares sum to forecast minus reference (error under 0.0003 °C on real sessions); a session equal to the reference gets zero for every group; a steady session gets a smaller total push than a ramping hot one; changing heart rate after the explained minute changes nothing; missing sensors get exactly 0.

Why-lines on 3 val people (ens5, raw):

| Person (session) | Minute | Estimate / 20-min p90 / truth in 20 min | Line |
| --- | --- | --- | --- |
| 25 (neutral weather, peak 38.0) | 20 | 37.6 / 38.2 / 37.7 | Rising because heart rate is high at 121 bpm and body temperature climbed 0.2 °C in 10 min. |
| 25 | 40 | 37.8 / 38.3 / 37.7 | Holding steady near 37.8 °C. Biggest upward pressure: heart rate is high at 125 bpm. |
| 50 (heat, peak 38.6) | 20 | 37.7 / 38.3 / 38.3 | Rising because heart rate is high at 121 bpm and body temperature climbed 0.2 °C in 10 min. |
| 50 | 40 | 38.2 / 38.7 / 38.4 | Holding steady near 38.2 °C. Biggest upward pressure: heart rate jumped 20 bpm in 10 min. |
| 64 (heat, crosses 38.5, peak 39.0) | 20 | 37.8 / 38.5 / 38.3 | Rising because heart rate jumped 29 bpm in 10 min. |
| 64 | 52 | 38.4 / 38.8 / 39.0 | Holding steady near 38.4 °C. Biggest upward pressure: heart rate jumped 16 bpm in 10 min. |
| sim-00001-d08 (has weather) | 401 | 38.8 / 39.0 / n/a | Holding steady near 38.8 °C. Biggest upward pressure: moderate work, it's 30 °C WBGT and 6.7 h into the shift. |

Honest limits:
- Mendeley only has heart rate, so real-data lines only mention heart rate and body heat. Weather and workload reasons show up on simulated shifts and on live data that has them.
- Most plateau minutes read "Holding steady" because the model's median rarely says "rising" after the first 30 minutes, even when the real temperature creeps up (person 64 minute 52: median says steady, truth rose 0.6 °C). That is a forecast weakness, not an explanation bug.
- The forecaster's `kalman_ct` input has the Kalman estimate itself, so "body_heat" shares are often negative (a hotter Kalman makes the network expect a smaller correction). The wording filter only names drivers that push the same way as the direction, so this rarely shows.
- Twins are ignored. The explanation covers the ensemble mean before spread and conformal offsets.

## 2. Weaver-style ensemble weights (`ml/explain/weaver.py`)

Which observable signal: I used each member's median forecast made 20 minutes ago compared with the Kalman estimate now, as an expanding average per worker per shift. Weight = inverse mean squared error, shrunk toward equal: `n/(n+30) * raw + 30/(n+30) * equal`, n = minutes of evidence (equal for the first 20 minutes). Why this one over the NOW-head residual: it scores the quantity we care about (the 20-min forecast), and every minute gives a fresh fully-observed test. Known weakness: the Kalman is itself off by about 0.4 °C, so a member that corrects the Kalman looks worse. I picked it before scoring and did not tune on val.

Scored with `ml/evaluate.py`, ens5 raw, 10 val people, 19 sessions (equal weights reproduce the ens5 numbers in docs/results.md). "oracle" = same recipe scored against the true core temp: not usable live, only shows the ceiling.

| | equal | Weaver | oracle (ceiling) |
| --- | --- | --- | --- |
| NOW RMSE °C | 0.239 | 0.243 | 0.238 |
| +10 / +20 / +30 RMSE °C | 0.229 / 0.230 / 0.238 | 0.231 / 0.231 / 0.238 | 0.226 / 0.228 / 0.236 |
| +20 quantile loss | 0.0634 | 0.0633 | 0.0628 |
| p90 coverage +10/+20/+30 | 0.94 / 0.96 / 0.96 | 0.94 / 0.96 / 0.96 | 0.94 / 0.96 / 0.96 |
| Red caught / lead / false alarms per hr | 88% / 38.3 / 0.55 | 88% / 38.3 / 0.44 | 88% / 38.3 / 0.58 |

Per person at +20 min, Weaver minus equal: mean +0.0004 °C (95% CI -0.0016 to +0.0023), better for 5 of 10 people.

**Result: no gain.** Weaver is a wash. Even a perfect verifier (oracle) only gains 0.002 °C, so the 5 members are about equally good for every person and there is nothing to reweight. (Red false alarms 0.55 to 0.44 is one alert out of 8 episodes, noise.) Weaver weights move between 0.11 and 0.46 (equal = 0.20), so the weighting does something, it just does not help. Recommendation: keep plain averaging. Weaver is more likely to pay off when members differ more (for example twins on versus off, or the simulator and a worst-case twin as verifiers, as planned in the lecture notes).

## 3. Figures (`ml/figures/make_figures.py`, PNGs in `docs/figures/`, each under 200 KB)

All on val people, ens5 raw, v1 raw, and the "Kalman + error bars" baseline (`kalman_flat` from `ml/baselines.py`, error percentiles learned on train people).

1. `01_predicted_vs_measured.png`: 3 heat sessions (lowest, middle, and highest peak among those that cross 38.5: people 53, 50, 64). 20-min-ahead median with the 10-90 band, lined up under the measured temp.
2. `02_bland_altman.png`: now-estimate minus measured vs average, Kalman next to ens5. Typical error 0.41 vs 0.24 °C; 95% limits -0.88 to +0.72 vs -0.46 to +0.48.
3. `03_coverage_by_horizon.png`: share of truths under the 90th percentile for horizons 1 to 30. ens5 is 88% at 1 min, 96% from 12 min on (too cautious). v1 and Kalman are overconfident (76% to 90%).
4. `04_rmse_by_horizon.png`: ens5 0.23 to 0.24 °C flat, v1 0.25 to 0.31, Kalman 0.44 to 0.46.
5. `05_alert_timeline.png`: red alert vs real crossing of 38.5 °C. I picked the caught crossing with the **median** lead time (person 64, 26 min), not the prettiest. All caught leads: 2, 13, 25, 26, 50, 64, 88 min. On this example the Kalman alert fires 25 min ahead, almost the same, so the figure does not claim a lead advantage over Kalman (the aggregate table in results.md has the lead comparison).

Placement: the brief said main checkout `docs/figures/`. I wrote them to this worktree's `docs/figures/` and committed them on this branch instead, so merging does not hit untracked files in main. Copy them when you merge.

## Follow-ups for the main ML session (not done by me)

- Live system: `explain()` expects `signals` with `wbgt` / `workload` filled 30 minutes past `minute` if known (same as the forecaster). Session A's note says without known-future inputs the p90 runs about 0.35 °C high; the explanation will then blame heart rate and body heat.
- If you ever want twin-aware explanations, `explain` needs a `twins` argument passed into `m.encode`/`m.forecast` (one-line change in `_p90_delta`).
