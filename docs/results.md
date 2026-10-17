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
