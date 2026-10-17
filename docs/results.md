# Results log

Every model, scored by `ml/evaluate.py` on Mendeley **validation** people (never trained on). Test people are untouched until final numbers.

## v1 (2026-10-17): single GRU, pretrained on 9,000 sim shifts, fine-tuned on 35 real people

Command: `python -m ml.train --name v1 --seeds 1` then `python -m ml.evaluate --model ml/checkpoints/v1.pt`

| Metric (10 val people, 19 sessions) | Kalman best baseline | v1 |
| --- | --- | --- |
| NOW RMSE °C (95% CI by person) | 0.414 (0.33-0.48) | **0.236 (0.18-0.28)** |
| NOW bias °C | -0.077 | **-0.033** |
| NOW 95% limits of agreement | -0.88 to +0.72 | **-0.49 to +0.43** |
| +10 / +20 / +30 min RMSE °C | 0.445 / 0.456 / 0.457 | **0.254 / 0.282 / 0.311** |
| +20 min quantile loss | 0.1155 | **0.0723** |
| +10 / +20 / +30 coverage of p90 (want 0.90) | 0.84 / 0.90 / 0.90 | 0.82 / 0.88 / 0.89 |
| Yellow: caught, lead, false alarms/hr | 94%, 19.6 min, 0.17 | 94%, **25.0 min**, 0.20 |
| Red: caught, lead, false alarms/hr | 75-88%, 37-38 min, 0.41 | 75%, 35.2 min, **0.27** |

Notes:
- Raw ECTemp's own variance covers only 34-45% at the 90th percentile (should be 90%): it is badly overconfident.
- Val people were used for early stopping in fine-tuning, so these numbers are slightly optimistic. Final claims come from test people.
- Next: conformal calibration, 5-model ensemble.

## v1 + conformal calibration (2026-10-17)

`python -m ml.calibrate --model ml/checkpoints/v1.pt` (fit on the 10 calib people), scored on val:

| | v1 raw | v1 + conformal | target |
| --- | --- | --- | --- |
| p90 coverage +10 / +20 / +30 | 0.82 / 0.88 / 0.89 | 0.96 / 0.96 / 0.95 | 0.90 |
| Red caught | 75% (6/8) | 88% (7/8) | |
| Red false alarms / hr | 0.27 | 0.61 | |

Swap check (`--swap-check`): fit on calib -> val coverage 0.96 / 0.96 / 0.95; fit on val -> calib coverage 0.84 / 0.81 / 0.80. Average is about right (~0.89), but 10 people is too few: person-to-person differences dominate. Plan: final calibration on calib + val (20 people) with the ensemble, then personal twins to shrink person-to-person error.

## ens5: 5-model deep ensemble (2026-10-17)

`python -m ml.train --name ens5 --seeds 5`, scored raw (uncalibrated) on val people:

| | Kalman best | v1 | ens5 |
| --- | --- | --- | --- |
| NOW RMSE °C | 0.414 | 0.236 | 0.239 |
| NOW bias °C | -0.077 | -0.033 | **+0.007** |
| +10 / +20 / +30 RMSE °C | 0.445 / 0.456 / 0.457 | 0.254 / 0.282 / 0.311 | **0.229 / 0.230 / 0.238** |
| +20 quantile loss | 0.1155 | 0.0723 | **0.0634** |
| p90 coverage +10/+20/+30 | 0.84 / 0.90 / 0.90 | 0.82 / 0.88 / 0.89 | 0.94 / 0.96 / 0.96 |
| Red caught / false alarms per hr | 75-88% / 0.41 | 75% / 0.27 | **88%** / 0.55 |

Ensemble forecasts ~50% better than ECTemp and barely degrade with horizon. Ranges now too wide (ensemble spread + model ranges); final calibration on calib + val (20 people) will tighten them. Swap check on ens5: calib->val 0.95-0.96, val->calib 0.81-0.86.

## Twins v1 (twins16): NEGATIVE result on real people (2026-10-17)

`python -m ml.twins --model ml/checkpoints/twins16.pt --proof real`: 20 unseen Mendeley people, adapt the 16-number twin on the neutral session's heart rate (network frozen, gate on held-out HR), score the heat session.

| Heat sessions (19) | average person | personal twin |
| --- | --- | --- |
| NOW RMSE °C | **0.301** | 0.423 |
| +20 min RMSE °C | **0.353** | 0.528 |
| Red caught | **60%** | 50% |

Gate kept 5 of 19 twins. Those improved heart-rate prediction but made core temp worse: the gate's signal (HR) is misaligned with the goal (core temp), the course's misaligned-verifier lesson. The 16-dim twin space learned in the simulator does not transfer to real people as-is.

Fix in progress: (1) CAVIA-style meta-training in the simulator so HR-based adaptation provably improves core temp, (2) a small physiology-based twin (3-4 numbers), (3) a stricter gate that also checks core temp in simulator replays. Until then, twins stay OFF in the live system.

## Twins v2 (meta4): CAVIA meta-learned 4-number twin (2026-10-17)

`python -m ml.meta --name meta4` then `--proof ml/checkpoints/meta4.pt`: 20 unseen real people, adapt on one session's HR (3 steps), score the other session, both directions (38 sessions).

| | average person | meta twin |
| --- | --- | --- |
| NOW RMSE °C | 0.297 | 0.297 |
| +20 min RMSE °C | 0.326 | 0.327 |
| Red caught | 69% | 69% |

Meta-learning removed the harm from twins v1 (0.30 -> 0.42), but there is no gain yet: the twin stays near the average person. Twins stay OFF live. Next ideas: more inner steps or a larger LR_INNER, adapt on more than one session, and a twin targeted at the personal HR offset.

Other sessions (their branches, not merged): G curriculum loop works but hasn't improved real-people scores yet; H Weaver weights gave no gain over equal weights, explain() and 5 figures done; F agent loops built (29 tests), curriculum bank made for $0.34, ACE/GEPA runs not done yet.

## Personalization diagnosis + the twin that works: personal offset from thermometer spot checks (2026-10-17)

Diagnosis on 19 unseen real people (ens5): per-session mean error SD 0.276 °C (most error is a per-person offset); a person's offset repeats across sessions (corr +0.57); resting heart rate cannot predict it (corr -0.02). So heart-rate twins learn from the wrong signal. Fix: learn the offset from quick thermometer spot checks (aligned verifier).

| Real people, apply 3 spot checks from one session to the other | NOW RMSE | +20 min RMSE |
| --- | --- | --- |
| no correction | 0.306 | 0.308 |
| person offset from spot checks | **0.285** | **0.270** |
| start-of-shift check only (decays) | 0.291 | 0.287 |
| start check fading to person offset | 0.278 | 0.267 |

Implemented as backend/models/personal_offset.py (Bayesian shrinkage toward the average person, more confident with every check, capped at 0.6 °C, bad readings ignored). Needs a cheap ear/oral thermometer and a spot-check step at shift start and on breaks.

## Hyperparameter search: 30 random trials, single models, scored on val (2026-10-17)

`python -m ml.search --trials 30`. Objective = +20 min quantile loss + 0.5 x NOW RMSE. Top 5 (these become the final mixed ensemble):

| rank | NOW RMSE | +20 RMSE | +20 q-loss | p90 cov +20 | red caught | settings |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.213 | 0.222 | 0.0563 | 0.89 | 75% | 128x1, dropout 0.0, lr 0.0005, w_nll 1.0, w_safety 0.5, w_smooth 0.3, sim.npz |
| 2 | 0.226 | 0.224 | 0.0565 | 0.91 | 88% | 48x2, dropout 0.2, lr 0.0005, w_nll 0.1, w_safety 0.25, w_smooth 3.0, sim_v2.npz |
| 3 | 0.225 | 0.226 | 0.0583 | 0.90 | 75% | 128x1, dropout 0.1, lr 0.0005, w_nll 0.3, w_safety 0.5, w_smooth 0.3, sim.npz |
| 4 | 0.232 | 0.232 | 0.0597 | 0.89 | 75% | 48x1, dropout 0.1, lr 0.0005, w_nll 1.0, w_safety 0.25, w_smooth 1.0, sim_v2.npz |
| 5 | 0.222 | 0.252 | 0.0650 | 0.96 | 100% | 96x3, dropout 0.0, lr 0.002, w_nll 0.3, w_safety 1.0, w_smooth 1.0, sim.npz |

Worst trial: NOW 0.356, +20 0.263. Best single model beats the 5-model ens5 (0.239 / 0.230). 2 of the top 5 pretrained on sim_v2 (pulse-sensor noise).

## FINAL: final_cv on the locked TEST people (2026-10-17, scored once)

`python -m ml.final_cv` then `python -m ml.evaluate --on test --model ml/checkpoints/final_cv.pt`. 5 members (top-5 search configs), cross-fitted over the 55 non-test people (5 folds of 11), CV+ conformal calibration from out-of-fold misses of all 55. The 10 test people (20 sessions) were never used for training, early stopping, model choice or calibration. Out-of-fold p90 coverage before/after calibration: 0.85 -> 0.90 at every horizon.

| On 10 unseen test people | Kalman (ECTemp) raw | Kalman + trend (best baseline) | **final_cv** |
| --- | --- | --- | --- |
| NOW RMSE °C (95% CI by person) | 0.398 (0.33-0.44) | 0.398 | **0.347** (0.21-0.40) |
| +10 / +20 / +30 RMSE °C | 0.442 / 0.481 / 0.500 | 0.384 / 0.380 / 0.400 | **0.363 / 0.370 / 0.371** |
| +20 quantile loss | 0.1582 | 0.0988 | **0.0882** |
| p90 coverage +10/+20/+30 (want 0.90) | 0.56 / 0.52 / 0.48 | 0.94 / 0.98 / 1.00 | 0.96 / 0.95 / 0.95 |
| 10-90 coverage (want 0.80) | 0.25 | 0.92-0.98 | 0.87-0.88 |
| Yellow caught, lead, false alarms/hr | 12%, 29 min, 0.03 | 88%, 13.0 min, 0.23 | 88%, **16.9 min**, **0.20** |
| Red caught, lead, false alarms/hr | 70%, 18 min, 0.26 | **100% (10/10)**, 36 min, 0.30 | 90% (9/10), 32 min, 0.46 |

What this honestly says:
- vs the plain Kalman filter (the published method): forecasts 18-26% more accurate at +10 to +30 min, NOW 13% better, and the ranges are actually right (raw Kalman's p90 covers only about half the time).
- vs our own Kalman + trend baseline: only 3-7% better on accuracy and 11% better on quantile loss. On red alerts it caught 9 of 10 vs 10 of 10, with more false alarms. 10 episodes is a tiny sample (one episode = 10 points), but we report it as is.
- Validation numbers (ens5 +20 error 0.230) were optimistic: those people were used to choose the model. Test error 0.370 is the number to quote.
- Ranges are slightly wide (p90 0.95 vs 0.90 wanted): errs on the safe side.
- Personal offset from thermometer spot checks comes on top of this (unseen people, +20: 0.308 -> 0.270 in its own test).
- Not tuned on test after scoring. Any change to alert rules must be chosen on out-of-fold data, never on these 10 people.
