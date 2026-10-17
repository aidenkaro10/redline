# Interface: ml-explain (why-lines for the forecast)

For the live system. One function, no new libraries (numpy + torch, already used by the forecaster).

```python
from backend.models.forecaster import ForecasterPredictor
from ml.explain.explain import explain

predictor = ForecasterPredictor.load("ml/checkpoints/ens5.pt")      # load once at startup
out = explain(signals, minute, predictor)                          # about 0.1 to 0.3 s on a laptop CPU
```

## Inputs

| Name | What |
| --- | --- |
| `signals` | Same dict you give `predictor.predict_signals`: 1-minute numpy arrays `hr` (required), and optionally `skin_temp`, `activity`, `wbgt`, `workload`. NaN = not measured. |
| `minute` | Index of the minute to explain (0 = first minute of the shift). Only data up to this minute is used as "the past". |
| `predictor` | A loaded `ForecasterPredictor`. The whole ensemble is explained as one. |

Known-future weather and planned work: `wbgt` and `workload` values for minutes `minute+1 ... minute+30` are read as the forecast weather and planned workload (same as the forecaster). Put them in the arrays after `minute`. Leave NaN if unknown; the explanation then gives them zero credit. Heart rate, skin temp and activity after `minute` are ignored (tested).

## Output

```python
{
  "why": "Rising because heart rate jumped 18 bpm in 10 min and it's 31 °C WBGT.",
  "direction": "rising",              # "rising" | "steady" | "falling"
  "contributions": {                  # °C, positive = pushes the 20-min 90th percentile up
      "heart_rate": 0.21, "body_heat": -0.04, "skin_temp": 0.0, "activity": 0.0,
      "wbgt": 0.12, "workload": 0.05, "time": 0.01},
  "now_c": 38.1,                      # current estimate
  "forecast_p90_c": 38.7,             # ensemble 20-min 90th percentile (no spread/conformal offsets)
  "median_rise_c": 0.2,               # median 20-min forecast minus now_c (drives "direction")
  "p90_rise_c": 0.62, "reference_p90_rise_c": 0.44,
  "convergence_error_c": 0.0001,      # leftover from the maths; should be under 0.01
}
```

## What the numbers mean

- It explains `p90_rise_c`: how far the 20-minute 90th-percentile forecast sits above the current estimate. The contributions add up to `p90_rise_c - reference_p90_rise_c`, the difference from a calm reference worker (65 bpm, WBGT 20 °C, light work, just started). Because "now" cancels the Kalman estimate, "body_heat" means "what the body-heat history adds to the expected change", not "you are already hot".
- `direction` uses the median forecast: rising if it is at least 0.15 °C above now, falling if 0.15 °C below, else steady. A steady line names the biggest upward pressure and says where the estimate sits, so it still makes sense when a yellow alert fires on the 90th percentile.
- Wording only names a group if it pushes the same way as the direction and carries at least a quarter of the biggest push (max 3 reasons).
- The `why` line is for explaining, never for deciding. Alerts and safety limits stay in `backend/config.py`.

## Known limits

- Real Mendeley data only has heart rate, so on those sessions only heart rate and body heat can be named. Weather and workload reasons appear on simulated shifts and live data that carry them.
- Twins are ignored (average person). The explanation describes the ensemble's network output, not the conformal offsets.
- Integrated gradients with 32 steps: the sum check is within about 0.0003 °C on tested sessions.
