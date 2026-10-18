# Session I: local planner (Qwen3-1.7B + LoRA)

Branch `claude/local-planner-ad9ed5`. Owns `ml/local_planner/`. Status: **done.** Teacher data collected, adapter trained, held-out evaluation run against the optimizer and Claude.

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

1. **Training days only.** 263 days: B's training generator (80), new seeds (40), F's curriculum bank plus new-seed variations (143). Code refuses `test-*` ids and any day whose content matches a held-out test day.
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
| `test_local_planner.py` | 8 pytest tests |
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
6. Run the tests:
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

A 1.7B model running on the laptop, trained on $22 of Claude's verified answers, plans as well as Claude inside our rule-checked pipeline. It costs $0 per plan and works offline, and workers' health data never leaves the site. On its own, without the checker, it's no safer than Claude, and neither is safe. The checker is the product, not the model. These are planning-model numbers on synthetic days, not real people.
