# Session I: local planner (Qwen3-1.7B + LoRA)

Branch `claude/local-planner-ad9ed5`. Owns `ml/local_planner/`. Status: **done.** Teacher data collected, adapter trained, held-out evaluation run against the optimizer and Claude. Round 2 ($0, no Claude calls) tried constrained decoding, an edit format, a repair loop and one round of self-training: see "Round 2" below.

## The answer

**Recommendation: the fine-tuned local model can be the planning pipeline's LLM** (`PLANNER_LLM=local`, schedules and the 6 PM briefing). Inside the pipeline it's as good as Claude: about the same work done and fairness, 0 safety violations, similar speed, $0 per plan, and crew health data never leaves the laptop. **Keep Claude for the chat agent**: it needs tool use, which the local model doesn't do.

What it does NOT show:
- It doesn't beat Claude. It ties, within noise, on 12 test days with 1 run each.
- The fine-tune isn't why the pipeline ties. The **base** model ties too, because the optimizer and the rule checker do most of the work. What the fine-tune buys: explanations that read like Claude's, its own schedule passing the checker 12/12 inside the pipeline (base 5/12), and real (but weak) standalone planning.
- On its own (no checker) it is not a planner: 43% valid plans, and the ones that pass are cautious (35% of the work done).

This is Aiden's call (it changes B's default). B owns the switch; the change is described at the end.

## Setup (approved by Aiden)

| | |
| --- | --- |
| Model | Qwen3-1.7B, Apache 2.0, text only. Runs as `mlx-community/Qwen3-1.7B-4bit` (0.98 GB) from `ml/checkpoints/local_planner/Qwen3-1.7B-4bit` (not in git) |
| Method | LoRA on the 4-bit model (QLoRA): rank 16, all 28 layers, 1% of weights trainable, loss on answers only, 2 epochs, LR 1e-4 cosine. Adapter = 70 MB in `ml/checkpoints/local_planner/adapter` (not in git) |
| Libraries | `mlx-lm` 0.32.2 (Apple, MIT) in the shared `.venv`. It pulled in `mlx` 0.32.4, `transformers` 5.20, `datasets`, `tqdm`, `sentencepiece` and friends |
| Hardware | Apple M5, 16 GB. Training on the GPU: 98 min. Planning one day: ~60 s (35 s of it is the local model) |

## How it was trained (STaR style)

1. **Training days only.** 263 days: B's training generator (80), new seeds (40), F's curriculum bank plus new-seed variations (143). Code refuses `test-*` ids and any day whose content matches a held-out test day. The content check hashes only a day's core facts (site, start time, weather numbers, crew bodies/skills/availability, tasks, hazard windows), not names or optional add-on fields. That way new planner fields (like B's round-3 storm chances) can't hide a copy of a test day. Re-checked after that change: 0 of the 263 trained-on days match a test day, by core content or by weather alone.
2. **Teacher = B's Claude agent** (Opus 5.5, optimizer + rule checker). One change: Claude's critic step was skipped (half the cost, prompts too long for a small model; the code critic still runs). Every prompt, answer and cost was saved.
3. **Keep only verified, good answers.** A day is kept if the agent's final plan passed the rule checker, isn't the all-rest fallback, does at least 30% of the work, and ranks at least as high as every optimizer-only plan.
4. **Three examples per day**, one per job the LLM has in the pipeline:
   - `plan`: B's "write the schedule" prompt → verified plan + short notes. Claude's own schedule when it passed and scored as well (16 days), otherwise the agent's final plan (STaR "rationalization").
   - `ideas`: the agent's strategy prompt → Claude's ideas, with the verified plan as `direct_plan`. Never a failing plan.
   - `explain`: the agent's "explain to the crew lead" prompt → Claude's bullets.
5. **Shorter prompts.** The ideas prompt carries the optimizer's plan as a big per-slot dump. `prompts.py` compacts it to plain assignments, in the training data and in `LocalLLM`, so B's code stays the same. It cut ideas prompts from ~5k to ~3k tokens. The first try at 8k-token examples pinned 14 GB and made the Mac swap.

| Teacher data | |
| --- | --- |
| Days run | 263 |
| Agent's final plan passed the rule checker | 262 / 263 |
| Kept by the filter | 224 (39 dropped: under 30% of the work) |
| Examples | 593 train + 78 valid (split by day), 1.19M tokens |
| Claude offered its own schedule | 99 / 263 days |
| ...and it passed the checker | 58 / 99 (59%). Failures: heat 39, shape 2, lightning 1 |
| Validation loss | 1.94 → 0.565 |

## Held-out results (B's 12 test days, never trained on)

**1. First try, no rule checker** (the model writes the whole schedule from B's `llm_only` prompt, 5 samples per day)

| Planner | Runs | Valid | Pass every rule | Runs with safety violations | Safety violations / run | Work done (all runs) | Work done when it passed | Latency (s) | Cost $/plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 alone (B's `llm_only`) | 60 | 100% | 8% | 92% | 3.1 | 0.87 | 0.82 | 21.7 | 0.052 |
| Qwen3-1.7B base | 60 | 0% | 0% | 3% | 0.6 | 0.00 | - | 6.1 | 0 |
| Qwen3-1.7B + LoRA, first adapter (172 days) | 60 | 43% | 8% | 83% | 3.8 | 0.24 | 0.45 | 4.8 | 0 |
| **Qwen3-1.7B + LoRA, final** | 60 | 43% | **17%** | **63%** | **2.0** | 0.20 | 0.35 | 7.4 | 0 |

- The fine-tune took the model from useless (0% valid) to passing every safety rule twice as often as Claude alone.
- But its passing plans rest people a lot (35% of the work vs Claude's 82%), and most of its plans break bookkeeping rules: tasks outside their time window, or a worker without the skill.
- Neither model should ever plan alone. The safety numbers here are exactly why the rule checker exists.

**2. Full planner, rule checker in the loop** (what ships; same solver settings for every row, 1 run per day)

| Planner | Safety violations after checker | Work done | Peak p90 (°C) | Heat spread (lower = fairer) | B's plan score | LLM's own schedule passed | Latency (s) | Cost $/plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Optimizer only (no LLM) | 0 | **0.68** | 38.00 | 31 | 0.382 | - | **12.7** | 0 |
| Claude agent (Opus 5.5) | 0 | 0.67 | 37.99 | **20** | **0.435** | 4 / 6 offered | 67.3 | 0.155 |
| Agent with Qwen3-1.7B base | 0 | 0.67 | 37.99 | 24 | 0.425 | 5 / 12 | 65.2 | 0 |
| **Agent with Qwen3-1.7B + LoRA** | 0 | 0.66 | 37.99 | 21 | 0.431 | **12 / 12** | 61.3 | **0** |
| Best of 5 local samples + optimizer plan, checker picks | 0 | 0.68 | 38.00 | 31 | - | local won 0 / 12 days | 49.7 | 0 |

- "B's plan score" is the agent's own ranking: work done, minus a penalty for heat near the limit and for unfair heat exposure.
- Safety violations after the checker: 0 in every row, as they must be.
- B's original benchmark (8 solver threads, wall clock) had Claude's agent at 0.65 work and 23 spread. We re-ran it under our settings (2 threads, deterministic solver budget) so every row is comparable. That re-run cost $1.86.
- The LLM matters little here. The agents beat the optimizer alone on fairness (spread 20-24 vs 31), mostly through the pipeline's ranking and fusion. Base vs fine-tuned vs Claude differ by a hair.
- The fine-tuned model's own schedule passes 12/12 in the pipeline because it learned to return a verified plan when it sees one. It was never the plan picked in the end, though (picks: fused 6, optimizer 4, its strategy ideas 2). Claude's own schedule was picked on 2 days.
- Explanations: the fine-tuned model's crew-lead bullets read like Claude's. The base model's are rougher. They aren't checked by code. The planner always adds a code-computed facts line (peak, margin, work done), so the numbers never depend on the model.

**API cost (all of it): $23.57.** Teacher $21.71 (527 calls), Claude re-run on the test days $1.86 (36 calls). Cap was $30. Log: `ml/local_planner/data/cost_log.jsonl`.

## Round 2: making the standalone model better at $0

Goal (from the main session, approved by Aiden): a local model that writes schedules that pass the rule checker AND do real work, without new Claude calls. Every option was measured on the 28 **validation** days only (4 samples a day); the held-out days were scored once at the end.

"Good pass" = passes every rule AND does at least the optimizer-only plan's work minus 0.05. That's the number that matters: a plan that rests everyone always passes and is useless.

**What each idea did (validation days; the optimizer alone does 0.66 of the work)**

| Variant | Adapter | Valid | Pass every rule | Good pass | Work when passing | s / plan |
| --- | --- | --- | --- | --- | --- | --- |
| Free writing (what we had) | original | 79% | 22% | 3.6% | 0.44 | 3.6 |
| Constrained decoding: format | original | **100%** | 22% | 2.7% | 0.36 | 4.8 |
| Constrained decoding: format + simple rules | original | 100% | 37% | 3.6% | 0.46 | 4.9 |
| ... + repair loop (show violations, 3 tries) | original | 100% | 56% | 3.6% | 0.45 | 19.9 |
| Edit the optimizer's plan, untrained | original | 0% parsed (falls back to the optimizer plan) | | | | |
| Edit format, after training on edits | edit | 100% | 79% | 78%* | 0.66 | 3.3 |
| Edit format + repair | edit | 100% | 91% | 88%* | 0.66 | 8.0 |
| Format + simple rules | edit | 100% | 54% | 7.1% | 0.43 | 4.4 |
| **Format + simple rules** | **round 1 (self-trained)** | 100% | 50% | **11.6%** | **0.51** | 4.5 |
| Free writing | round 1 | 83% | 41% | 4.5% | 0.41 | 3.5 |

\* Not a real gain: 76% of edit answers changed nothing, so they're just the optimizer's plan. Of the 27 answers that did change the plan, 3 passed, and they scored below the optimizer. The edited plan beat the optimizer's plan score on 1 of 112 samples.

What we learned:
1. **Constrained decoding** (`constrained.py`) works: 100% valid schedules, no new libraries (an mlx-lm logits processor walks the JSON skeleton and only allows legal ids). "Format" stops bookkeeping errors; "format + simple rules" also blocks storm-window work, very heavy work for new hires and too-long work streaks. Heat (the 38.0 °C forecast) can't be checked token by token, and every remaining failure is heat. The rule checker still checks every plan afterwards.
2. **Repair loop** raises the pass rate (37% → 56%) but not the good-pass rate: the model fixes heat problems by resting people.
3. **Edit, don't write** is safe (it can always fall back to the optimizer's verified plan) but adds nothing: the model can't find edits that improve an already-optimized plan. Not worth shipping.
4. **Self-training (expert iteration)** became possible once constrained decoding raised the good-pass rate. Round 1: 100 training days × 8 samples (rules-constrained), 6% good; kept the best good sample on 28 days, counted 3× next to 498 replayed Claude examples (plan, ideas, explain and edit jobs, so it doesn't forget any), one pass from the edit adapter. Good pass on validation 7.1% → 11.6%, and passing plans do more work (0.43 → 0.51). We stopped after one round, as agreed.
5. The first self-training attempt (plain sampling, original adapter) found good samples in only 3.6% of tries, and was stopped at 20 of 100 days when mlx's GPU buffer cache grew to ~11 GB and the Mac swapped. Nothing from it was used. `client.py` now caps the mlx cache at 1 GB.

**Held-out days (B's 12 test days, scored once; 5 samples per day; optimizer alone: 0.69 work)**

First try, no rule checker in the loop:

| Planner | Valid | Pass every rule | Good pass | Work when passing | Safety violations / plan | s / plan | $ / plan |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 alone | 100% | 8% | **8.3%** | **0.82** | 3.1 | 21.7 | 0.052 |
| Qwen base, free | 0% | 0% | 0% | - | 0.6 | 6.1 | 0 |
| Original adapter, free | 43% | 17% | 0% | 0.35 | 2.0 | 7.4 | 0 |
| Original adapter, format + simple rules | 100% | 43% | 6.7% | 0.48 | 1.2 | 3.9 | 0 |
| Round-1 adapter, free | 58% | 28% | 1.7% | 0.37 | 2.3 | 3.3 | 0 |
| **Round-1 adapter, format + simple rules** | 100% | **57%** | 5.0% | 0.50 | 0.9 | 4.0 | 0 |
| **Round-1 adapter, + repair loop** | 100% | **68%** | **8.3%** | 0.50 | 0.7 | 14.1 | 0 |

Full planner (rule checker in the loop, 1 run per day, same solver settings):

| Planner | Safety violations | Work done | Heat spread | Plan score | LLM's own schedule passed | s / plan | $ / plan |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Optimizer only | 0 | 0.68 | 31 | 0.382 | - | 12.7 | 0 |
| Claude agent | 0 | 0.67 | 20 | 0.435 | 4 / 6 offered | 67.3 | 0.155 |
| Agent + original adapter | 0 | 0.66 | 21 | 0.431 | 12 / 12 | 61.3 | 0 |
| **Agent + round-1 adapter** | 0 | 0.66 | 21 | 0.429 | 8 / 12 | **49.2** | 0 |

Honest read:
- **Better, and not lazier, but not good.** On held-out days the round-1 adapter with constrained decoding passes every rule 3-4× as often as before (57-68% vs 17%), and its passing plans do more work (0.50 vs 0.35). With the repair loop it matches Claude alone on good passes (8.3%).
- **Still far from the optimizer.** Its passing plans do 0.50 of the work; the optimizer does 0.69. Claude's rare passes do 0.82. Neither LLM should ever plan without the optimizer and the checker.
- The validation gain in good passes (7.1% → 11.6%) didn't fully carry over to the 12 held-out days (6.7% → 5.0% without repair, 8.3% with it). 60 samples is a small sample.
- **In the full pipeline nothing changed**: plan score 0.429 vs 0.431, within noise of Claude's 0.435, and it's the fastest (49 s). The pipeline uses free writing for the LLM's own schedule; constrained decoding isn't wired into it.

**Are the explanations factually right? (no LLM judge; `explain_check.py`)** Every number, clock time and worker name in the crew-lead bullets has to be backed by the facts the model was given or by the day itself.

| Days | Model | Answers | Claims checked | Unsupported |
| --- | --- | --- | --- | --- |
| Validation (same facts for both, never trained on) | Claude Opus 5.5 | 28 | 108 | **0** |
| Validation | Original adapter | 28 | 86 | **0** |
| Held-out (full facts saved) | Round-1 adapter | 12 | 33 | **0** (1 flag, a false positive: "not at 100%") |
| Held-out (only the final plan's facts saved) | Claude Opus 5.5 | 12 | 54 | 12, nearly all numbers about the alternative plans, which this check can't see |

- Headline: on the same facts, the local model's explanations are as factually clean as Claude's.
- The free-text `notes` the model writes next to a schedule (without facts) are NOT reliable: the few percentages in them were wrong (for example "about 93%" for a plan doing 71%). Redline should show the code-written facts line, not those notes.
- One wording issue for Redline: an explanation called a worker "his" (a pronoun guessed from a name). Add "refer to workers by name or as they" to the explain prompt (B's `archon.explain`).

**Adapters** (all in `ml/checkpoints/local_planner/`, none overwritten): `adapter` (original), `adapter_edit` (edit format), `adapter_st1` (round 1, recommended). Pick one with `LOCAL_PLANNER_ADAPTER`.

**Cost of round 2: $0.** GPU time ~6 h, CPU at most 3 processes.

## Files

| File | What it does |
| --- | --- |
| `scenarios.py` | Training pool (never test days, checked by id and by content) |
| `teacher.py` | Claude agent on the pool, hard $ cap shared across processes, resumes |
| `dataset.py` | STaR filter → chat examples (`plan`, `ideas`, `explain`) |
| `prompts.py` | Compacts the plan view in ideas prompts (training and running) |
| `train.py` | Waits for the main ML session, builds the dataset, runs `mlx_lm lora` |
| `client.py` | `LocalLLM`: same interface as B's `LLM`, $0, no tool use |
| `evaluate.py` | Held-out comparison (both tables above) |
| `constrained.py` | Constrained decoding (legal schedule JSON only), mlx-lm logits processor |
| `edits.py` | Edit format: prompt, applying edits, edit training targets, optimizer plans per day |
| `variants.py` | Validation (and the one held-out) runner for every variant, incl. the repair loop |
| `self_train.py` | Expert iteration: sample, rule-check, keep good plans, retrain |
| `train_v2.py` | Retrain on the edit format with replay of all jobs |
| `explain_check.py` | Fact check of the crew-lead explanations |
| `test_local_planner.py` | 14 pytest tests |
| `data/` | Cost log, eval results (`eval/`), `teacher_runs.tar.gz` (all 263 runs), `sft.tar.gz` (training set) |

## How to run it

From the repo root after merging. Default paths: `data/local_planner/`, `ml/checkpoints/local_planner/`.

1. Download the base model if `ml/checkpoints/local_planner/Qwen3-1.7B-4bit` is missing (0.98 GB):
   ```bash
   .venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/Qwen3-1.7B-4bit', local_dir='ml/checkpoints/local_planner/Qwen3-1.7B-4bit')"
   ```
2. Unpack the committed teacher runs (only needed to retrain):
   ```bash
   mkdir -p data/local_planner && tar -xzf ml/local_planner/data/teacher_runs.tar.gz -C data/local_planner
   ```
3. Retrain the adapter (~100 min on the GPU; skip if `ml/checkpoints/local_planner/adapter/adapters.safetensors` exists):
   ```bash
   .venv/bin/python -u -m ml.local_planner.train
   ```
4. Plan one held-out day with the local model inside B's agent:
   ```bash
   .venv/bin/python -c "from backend.planner.generate import load_test_set; from backend.planner.archon import plan_with_agent; from ml.local_planner.client import LocalLLM; r = plan_with_agent(load_test_set()[3], llm=LocalLLM()); print(r.check.ok, r.summary); print(*r.reasons, sep='\n')"
   ```
   One test that it works: it prints `True` (the plan passed the rule checker) and 3-4 plain bullets for the crew lead.
5. Re-run the evaluation (prints both tables):
   ```bash
   .venv/bin/python -u -m ml.local_planner.evaluate --only first_try --models finetuned --runs 5
   ```
   ```bash
   .venv/bin/python -u -m ml.local_planner.evaluate --only pipeline --models finetuned,base
   ```
6. Round 2 (all $0). Validation check of any adapter and decoding:
   ```bash
   .venv/bin/python -u -m ml.local_planner.variants --adapter adapter_st1 --modes free,rules,rules+repair
   ```
   Retrain on the edit format, then one self-training round:
   ```bash
   .venv/bin/python -u -m ml.local_planner.train_v2
   ```
   ```bash
   .venv/bin/python -u -m ml.local_planner.self_train --round 1 --start adapter_edit --decode rules --replay data/local_planner/sft_adapter_edit
   ```
   Explanation fact check on the validation days:
   ```bash
   .venv/bin/python -u -m ml.local_planner.explain_check --adapter adapter_st1
   ```
7. Run the tests:
   ```bash
   .venv/bin/python -m pytest ml/local_planner -q
   ```

The teacher (`python -m ml.local_planner.teacher`) is done. Running it again spends nothing new: every day is already saved.

## Plugging it in (for B: B owns `backend/planner/`, not edited here)

1. In `backend/planner/llm.py`, `get_llm()` returns the local model when `.env` says `PLANNER_LLM=local`:
   ```python
   if os.getenv("PLANNER_LLM", "on").lower() == "local":
       from ml.local_planner.client import LocalLLM
       return LocalLLM()
   ```
   `llm_available()` should return True in that mode too (no API key needed).
2. In `backend/planner/agent.py` chat, treat `getattr(llm, "supports_tools", True) is False` like no-key mode. Or better, keep Claude for chat: build the chat LLM with `LLM()` directly when a key exists.
3. Nothing else changes. `plan_with_agent(..., llm=LocalLLM())` works as is, and every plan still goes through `rules.py`.

## For the pitch

A 1.7B model running on Redline's laptop, trained on $22 of Claude's verified answers, plans as well as Claude inside our rule-checked pipeline, and its explanations are as factually clean as Claude's on the same facts. It costs $0 per plan and works offline, and workers' health data never leaves the site. On its own, without the checker, it's no safer than Claude, and neither is safe. The checker is the product, not the model. These are planning-model numbers on synthetic days, not real people.
