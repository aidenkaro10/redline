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
