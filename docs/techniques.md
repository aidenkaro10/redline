# Every technique we used, by its standard name

One line per technique: what we did in plain words, then the standard name and where it comes from. Use the names in parentheses in the write-up and pitch. "Our design" means we made it up for this project, on top of standard pieces.

## Core temp right now

| What we did | Standard name | Source |
| --- | --- | --- |
| Turn heart rate into a live core temp estimate | Kalman filter (extended Kalman filter), ECTemp model | Kalman 1960; Buller et al. 2013, US Army (ECTemp) |
| Combine two heart rate sensors by how noisy each is | Sensor fusion (sequential Kalman updates) | Standard Kalman practice |
| Keep estimating when heart rate drops out | Predict-only Kalman step | Standard Kalman practice |

## Training data

| What we did | Standard name | Source |
| --- | --- | --- |
| 9,000 simulated work shifts from heat physics | Physics-based simulation (Predicted Heat Strain, ISO 7933) | ISO 7933; pythermalcomfort library |
| Heat number from weather (temp, humidity, sun, wind) | WBGT, Liljegren method | Liljegren et al. 2008 |
| Fake pulse-sensor noise in the simulator | Data augmentation (sensor artifact simulation) | Standard ML practice |
| Learn physics on fake data, then real people | Pretraining + fine-tuning (sim-to-real transfer learning) | Standard ML practice |
| Test only on people the model never saw | Subject-wise split (grouped split, no leakage) | Standard ML practice |
| 10 people locked away, scored once | Held-out test set | Standard ML practice |

## The forecaster (neural net)

| What we did | Standard name | Source |
| --- | --- | --- |
| Network that reads the last hour minute by minute | Recurrent neural network, GRU (gated recurrent unit) | Cho et al. 2014 |
| Reader for the past, writer for the next 30 min | Sequence-to-sequence encoder-decoder | Sutskever et al. 2014; Cho et al. 2014 |
| Weather forecast and planned work fed into the future | Known future covariates | Standard forecasting practice |
| Predict the correction to the Kalman, not the raw temp | Residual learning on a physics baseline (hybrid physics-ML) | Standard practice |
| Low / likely / high instead of one number | Quantile regression, pinball loss | Koenker and Bassett 1978 |
| Low never above likely, likely never above high | Non-crossing quantiles | Standard technique |
| "Right now" estimate with its own uncertainty | Gaussian negative log-likelihood | Nix and Weigend 1994 |
| Guessing too low costs extra, more when actually hot | Asymmetric (cost-sensitive) loss | Our design |
| Forecast line can't jump faster than a body can | Physiology smoothness penalty | Our design |
| Hide sensors at random while training | Input masking / sensor dropout augmentation | Standard ML practice |
| "Missing" flags for each sensor | Missing-value indicators | Standard ML practice |
| Stop training when it stops improving on checking people | Early stopping | Standard ML practice |
| Try 30 recipes, keep the best 5 | Random hyperparameter search | Bergstra and Bengio 2012 |

## Making it trustworthy

| What we did | Standard name | Source |
| --- | --- | --- |
| 5 networks vote, average their answers | Deep ensemble | Lakshminarayanan et al. 2017 |
| Stretch the ranges so "90% sure" really is 90% | Conformal prediction (conformalized quantile regression) | Vovk et al. 2005; Romano et al. 2019 |
| 55 people take turns training and checking | Cross-fitting (K-fold), CV+ / cross-conformal calibration | Barber et al. 2021 |
| Error bars on our score from resampling people | Cluster (by-person) bootstrap confidence intervals | Efron 1979 |
| Agreement plot of predicted vs real | Bland-Altman analysis | Bland and Altman 1986 |
| Accuracy and range scores | RMSE, bias, quantile loss, coverage | Standard metrics |
| Alert scores | Detection rate, lead time, false alarms per hour | Standard early-warning metrics |
| Code refuses to score a model on anyone it trained on | Leakage guard | Our design |
| Always shown next to simple methods | Baseline comparison (Kalman raw, persistence, linear trend) | Standard practice |
| Which inputs drove a forecast | Integrated gradients (feature attribution) | Sundararajan et al. 2017 |

## Personalization

| What we did | Standard name | Source |
| --- | --- | --- |
| Find that most error is a steady per-person offset | Error decomposition / residual analysis | Standard practice |
| 16 personal numbers learned from heart rate (failed) | Learned per-person embeddings, self-supervised target | Standard technique |
| Network trained to adapt fast with 4 numbers (no gain) | Meta-learning, CAVIA | Zintgraf et al. 2019 |
| Offset from thermometer checks with "+2 imaginary checks" | Bayesian shrinkage (empirical Bayes) | Classic statistics |

## Site hazards

| What we did | Standard name | Source |
| --- | --- | --- |
| Tomorrow's 0-10 hazard score per site | Gradient-boosted trees (HistGradientBoostingClassifier), logistic regression baseline | scikit-learn |
| Lightning stop-work rule | Rule-based alerting (10 mi, 30 min all-clear) | Standard lightning safety practice |

## Planner

| What we did | Standard name | Source |
| --- | --- | --- |
| Find the best schedule among billions | Constraint programming, CP-SAT solver | Google OR-Tools |
| Predict each worker's heat for every candidate schedule | Predict-then-optimize | Standard operations research |
| Claude suggests, code checks every plan | Generator-verifier pattern (LLM with a code verifier) | Standard AI safety practice |
| Claude calls the forecaster and optimizer | LLM tool use (function calling) | Anthropic API |
| Several candidate plans, rule check, critic, combine, re-check | Generate-verify-critique-fuse pipeline (Archon-style inference-time architecture) | Saad-Falcon et al. 2024 |
| Re-plan during the day when forecasts change | Closed-loop replanning (model predictive control style) | Standard control practice |
| Auto-apply only changes that can only make things safer | Monotone (safer-only) autonomy, human approval for the rest | Our design |
| Penalty for outdoor work in likely-storm slots | Soft constraint (cost term) | Standard optimization |
| Search for the best heat vs. work balance | Random search + successive halving, Pareto front | Jamieson and Talwalkar 2016 |

## Self-improving agents

| What we did | Standard name | Source |
| --- | --- | --- |
| Claude invents practice days that are hard but doable | Self-play curriculum (Absolute Zero) | Zhao et al. 2025 |
| Agent writes and keeps a playbook of lessons | Agentic Context Engineering (ACE) | Zhang et al. 2025 |
| Claude rewrites the agent's strategy from its failures | Reflective prompt evolution (GEPA) | Agrawal et al. 2025 |
| Keep a family tree of good versions, explore new ones | Open-ended evolution with an archive (Darwin Gödel Machine) | Zhang et al. 2025 |
| Keep a change only if it wins on unseen days | Validation gate (held-out selection) | Standard ML practice |
| Score candidates with several weak checkers combined (no gain) | Weak verifier ensembling (Weaver) | Saad-Falcon et al. 2025 |
| Small open-source model that copies Claude's plans offline | Knowledge distillation + LoRA fine-tuning (Qwen3-1.7B, 4-bit, MLX) | Hinton et al. 2015; Hu et al. 2021 |

## Band sensors (laptop side)

| What we did | Standard name | Source |
| --- | --- | --- |
| Pulse from light through the skin | Photoplethysmography (PPG) | Standard technique |
| Keep only heart-rate frequencies | Band-pass filtering (0.7-3.5 Hz) | Standard signal processing |
| Find the heartbeat rate | Spectral peak + autocorrelation | Standard signal processing |
| Subtract arm movement using the accelerometer | Adaptive noise cancellation (NLMS filter) | Widrow et al. 1975 |
| Fall = jolt + flat + still, confirmed by a height drop | Threshold-based fall detection with barometric confirmation | Standard wearable practice |

## Emergency call

| What we did | Standard name | Source |
| --- | --- | --- |
| Voice agent reads facts only, answers "Unknown" otherwise | Grounded LLM voice agent with dynamic variables | ElevenLabs Conversational AI + Twilio |
| 911 and short codes blocked in code | Allowlist + blocklist (defense in depth) | Our design |
