# Session G: adversarial curriculum (Loop 4)

Branch `claude/ml-curriculum-03cd71`. Code in `ml/curriculum/` only. No changes to `ml/train.py`, `ml/simulate.py` or any shared file. Training is wrapped from `ml/curriculum/rounds.py` using `ml/train.py`'s own `to_tensors` and `train_stage`.

## What it does

| Piece | File | What |
| --- | --- | --- |
| Scenario knobs | `scenario.py` | 16 knobs: afternoon high, humidity, clouds, storm, wind, WBGT forecast error, heavy-job mix, fitness, acclimatization, clothing, stop temperature, body size, band pulse sensor (motion artifacts), heart rate gap rate, watch dies, skin diode off. Reuses `ml/simulate.py`'s PHS physiology, heart rate model and `add_ppg_motion_artifacts`. 288 shifts in ~11 s. |
| Proposer | `proposer.py` | Evolution search: 32 random scenarios, play each 6 times, score the model, mutate the most interesting, repeat (3 generations, ~30 s). |
| "Solved" | `proposer.py` | One shift is solved if, at the +20 min forecast, truth is under the 90th percentile on at least 80% of minutes AND the median's RMSE is at most 0.3 °C. |
| Learnability filter | `learnability.py` | Absolute Zero reward = 1 - solve rate, 0 if never or always solved (DAPO dynamic sampling). Keeps only "sometimes solved", most rewarding first, near-duplicates removed, max 40. |
| Round + gate | `rounds.py` | Fresh shifts for the kept scenarios (~10% of the base sim) go into the simulated training mix. Retrain from scratch (pretrain + fine-tune). Score on Mendeley VAL people. **Keep only if** mean quantile loss (+10/+20/+30) drops by more than `--min-gain`, NOW and forecast RMSE are not worse (0.005 °C tolerance), and red catch rate is not lower. Otherwise roll back. Every round goes to `ml/runs/curriculum/<name>/rounds.jsonl`. |
| Sim check | `check.py` | Not a gate. Scores models on held-out replays (new seed) of one round's hard scenarios, plus everyday shifts. Shows whether the curriculum fixed what it aimed at. |

Options: `--gate-seeds N` trains each round with N seeds and gates on the average (less luck). `--rehearse R` mixes R sim shifts per real session into fine-tuning, half of them from the curriculum. `--min-gain` sets how much quantile loss must improve.

## Results on Mendeley VAL people (10 people, 19 sessions), single models

Quantile loss = mean over +10/+20/+30. Forecast RMSE = mean of the median's RMSE over the three horizons. All models are raw (no conformal calibration).

### Run "small" (hidden 32, 2,000 base sim shifts, 4 pretrain epochs, normal recipe)

| Model | NOW RMSE °C | Forecast RMSE °C | +20 RMSE °C | Quantile loss | p90 cov +20 | Red caught | Red false/hr | Hard shifts in mix | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kalman flat (baseline) | 0.414 | 0.455 | 0.457 | 0.1156 | 0.90 | 75% | 0.41 | | |
| Kalman trend (baseline) | 0.414 | 0.472 | 0.467 | 0.1196 | 0.87 | 88% | 0.41 | | |
| v1 (main session, hidden 64) | 0.236 | 0.283 | 0.283 | 0.0724 | 0.88 | 75% | 0.27 | | |
| ens5 (main session, 5 models) | 0.239 | 0.232 | 0.230 | 0.0632 | 0.96 | 88% | 0.55 | | |
| Round 0 (base only) | 0.240 | 0.270 | 0.256 | 0.0685 | 0.88 | 62% | 0.27 | 0 | start |
| Round 0, 2nd seed (noise check) | 0.235 | 0.259 | 0.246 | 0.0678 | 0.94 | 75% | 0.34 | 0 | (noise) |
| Round 1 | 0.237 | 0.269 | 0.257 | 0.0680 | 0.89 | 62% | 0.38 | 170 | keep |
| Round 2 | 0.240 | 0.268 | 0.257 | 0.0675 | 0.88 | 62% | 0.31 | 345 | keep |
| Round 3 | 0.241 | 0.266 | 0.254 | 0.0674 | 0.90 | 62% | 0.31 | 505 | keep |

Proposer each round: 48 scenarios tried. Learnable 34 / 35 / 41, never solved 13 / 11 / 6, always solved 1 / 2 / 1.

All three rounds were kept, but the total gain (quantile loss 0.0685 to 0.0674) is about the same size as changing the seed alone (0.0685 vs 0.0678, and red catch 62% vs 75%). **Not real evidence the curriculum helps.** That's why `--min-gain` and `--gate-seeds` exist now. With `--min-gain 0.0007`, round 1 would have been rolled back.

### Why the proposer finds "hard" scenarios everywhere

`check.py` on held-out shifts (new seed). "Hard" = the 47 scenarios round 1 found hard. "Everyday" = default knobs.

| Model | Hard: solved / +20 RMSE | Everyday: solved / +20 RMSE |
| --- | --- | --- |
| small round 0 | 18% / 0.405 | 19% / 0.356 |
| small round 0, seed 2 | 54% / 0.332 | 61% / 0.291 |
| small round 1 | 23% / 0.409 | 25% / 0.368 |
| small round 3 | 26% / 0.388 | 35% / 0.337 |

Even ordinary field shifts are mostly "unsolved", and p90 coverage is 1.00. The forecasts run **high**. Bias of the +20 min median on 60 everyday sim shifts:

| Model | Bias °C | RMSE °C | Bias when truth >= 38 °C |
| --- | --- | --- | --- |
| small round 0 | +0.28 | 0.35 | +0.33 |
| small round 0, seed 2 | +0.17 | 0.28 | +0.25 |
| v1 | +0.12 | 0.17 | +0.15 |
| ens5 | +0.22 | 0.26 | +0.27 |

**Main finding:** fine-tuning on the 2-hour Mendeley lab sessions (heart rate only) pulls every model toward running hot on 8-hour field shifts. That's safe in direction (false alarms, not misses), and it matches session A's note that the 20-min p90 runs ~0.35 °C high in replay. The curriculum puts its shifts into pretraining, and fine-tuning then washes them out. So the proposer is mostly rediscovering this one bias, not rare hard cases.

### Run "small_reh": same, plus sim rehearsal in fine-tuning (`--rehearse 1.0 --gate-seeds 2`)

Numbers are the average of 2 seeds.

| Model | NOW RMSE °C | Forecast RMSE °C | +20 RMSE °C | Quantile loss | p90 cov +20 | Red caught | Red false/hr | Hard shifts | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Round 0 (base + rehearsal) | 0.272 | 0.284 | 0.279 | 0.0707 | 0.85 | 62% | 0.36 | 0 | start |
| Round 1 | 0.253 | 0.291 | 0.285 | 0.0729 | 0.82 | 56% | 0.22 | 170 | roll back |
| Round 2 | 0.261 | 0.288 | 0.282 | 0.0724 | 0.85 | 56% | 0.26 | 160 | roll back |
| Round 3 | 0.261 | 0.284 | 0.276 | 0.0715 | 0.88 | 62% | 0.27 | 165 | roll back |

Proposer: always solved 12 / 11 / 12 of 48 (vs 1 or 2 without rehearsal), never solved 2 / 5 / 3. The model is now good on most sim shifts, so the filter sorts real difficulty.

Sim check (same held-out sets):

| Model | Hard: solved / +20 RMSE | Everyday: solved / +20 RMSE |
| --- | --- | --- |
| rehearsal round 0, seed 0 / seed 1 | 75% / 0.274, 73% / 0.280 | 98% / 0.204, 92% / 0.229 |
| rehearsal round 3 (rolled back), seed 0 / seed 1 | 70% / 0.290, 73% / 0.280 | 87% / 0.242, 89% / 0.236 |

- **Rehearsal alone** is the big lever on field shifts: everyday solved goes from 19-61% to 92-98%. The cost is real val accuracy: quantile loss 0.068 to 0.071, NOW RMSE 0.24 to 0.27.
- **The curriculum on top of rehearsal did not help**, not even on its own hard scenarios. The gate rolled back all 3 rounds correctly.

### Run "full" (hidden 64, 9,000 base shifts, normal recipe, `--min-gain 0.0007`)

Pending: queued until the main session's training job finishes.

## Honest bottom line

1. The loop works: the proposer finds hard scenarios, the filter drops easy and hopeless ones, and the gate makes clear keep/rollback calls (small: 3 keeps; small_reh: 3 rollbacks).
2. The curriculum has **not** shown a real gain on real val people. Every gain is within seed noise.
3. The real problem it found: fine-tuning on lab data makes the model run 0.1-0.3 °C hot on field shifts (ens5 too). That's a decision for Aiden / the main ML session, not a curriculum fix:
   - (a) Keep as is: hot = safe direction, conformal calibration can't fix a bias that only shows in the field.
   - (b) Add sim rehearsal to fine-tuning: much better field shifts, slightly worse on lab val people.
   - (c) A field-shift validation set (sim or, later, our own band data) next to Mendeley val, so the gate sees both.
4. Mendeley val (2-hour lab, heart rate only, 8 red episodes) can't see most of what the proposer targets (pulse-sensor artifacts, watch dying, storms, coveralls). One red episode = 12.5%, so the red gate is coarse.
5. Val people are also used for fine-tune early stopping, and the gate picks on them too. Over many rounds that overfits val. Test people stay untouched.

## How to run

1. Open a terminal in the repo.
2. Run the tests: `pytest ml/curriculum -q` (8 tests, ~10 s).
3. Prove the loop with a small model (~7 min): `python -m ml.curriculum.rounds --name small --small --rounds 3 --noise-check --root /Users/Annabelle/Documents/projects/planit-control`
4. Watch it print each round's val numbers and `KEEP` or `ROLL BACK` with the reason.
5. Read the log: `ml/runs/curriculum/small/rounds.jsonl` (one line per round), `archive_rN.json` (every scenario tried), `mix.json` (accepted scenarios + seeds, enough to rebuild the shifts).
6. Full single model (~23 min per training on 4 threads): `python -m ml.curriculum.rounds --name full --rounds 2 --min-gain 0.0007 --root ...`. First check nothing else is training: `ps aux | grep -E "ml\.(train|meta|twins)"`.
7. Check the sim side: `python -m ml.curriculum.check --archive <root>/ml/runs/curriculum/small/archive_r1.json --model <root>/ml/checkpoints/cur_small_r0.pt --model <root>/ml/checkpoints/cur_small_best.pt`
8. Score a curriculum model next to the baselines: `python -m ml.evaluate --model ml/checkpoints/cur_full_best.pt:raw`

One test to verify: step 3 should end with `Done in ... Accepted rounds: [...]`, and `rounds.jsonl` should have a `decision` for every round.

For the main session's ensemble: `ml/curriculum/mixes/<name>.json` + `rounds.build_mix_sessions(mix)` rebuild the exact curriculum shifts. Add them to the sim list before pretraining.

## Time and CPU

- Proposer: ~20-40 s per round (simulating + scoring 144-288 shifts).
- Small model: ~0.5-2.5 min per training. A 3-round run takes 7 min (11 min with 2 seeds + rehearsal).
- Full model (hidden 64, 9,900 shifts): 88 s per pretrain epoch on 4 threads, up to 15 epochs ≈ 23 min per training. With 1 seed, 1 round ≈ 24 min, so **2-3 full rounds per hour** when the CPU is free. With `--gate-seeds 2`, ~1 per hour.
- The CPU is shared: the main session's `ml.meta` and other sessions' jobs were running at the same time, so the full run waited for them.
