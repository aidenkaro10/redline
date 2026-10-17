# Session I: local planner (Qwen3-1.7B + LoRA)

Branch `claude/local-planner-ad9ed5`. Owns `ml/local_planner/`. Status: **stopped at the feature freeze, before the fine-tune.** The pipeline is built and tested, the teacher data is partly collected, and the LoRA training has never run. No decision on the local model yet (see the end).

## Goal

Distill the planner's LLM into a small open model that runs on the laptop: worker health data never leaves the machine, it works offline, and we test whether a narrow fine-tuned model beats Claude on our task. The LLM still never makes safety calls. Every plan, from any model, goes through B's code rule checker.

## Setup (approved by Aiden)

| | |
| --- | --- |
| Model | Qwen3-1.7B, Apache 2.0, text only. Running copy: `mlx-community/Qwen3-1.7B-4bit` (0.98 GB) in `ml/checkpoints/local_planner/Qwen3-1.7B-4bit` (not in git) |
| Method | LoRA on the 4-bit model (QLoRA), rank 16, all layers, loss on answers only. Saves only the adapter (~20 MB) |
| Libraries | `mlx-lm` 0.32.2 (Apple, MIT) in the shared `.venv`. It pulled in `mlx` 0.32.4, `transformers` 5.20, `datasets`, `tqdm`, `sentencepiece` and friends |
| Why mlx | This Mac is Apple Silicon (M5, 16 GB). mlx trains on the GPU, one command |

## What's built

| File | What it does | Status |
| --- | --- | --- |
| `scenarios.py` | Training pool: B's train generator (80), new seeds (40), F's curriculum bank + new-seed variations (143) = 263 days. Refuses `test-*` ids AND any day whose content matches a held-out test day | Done, tested |
| `teacher.py` | Runs B's Claude agent (Opus 5.5) on the pool. Saves every prompt, answer and cost. Hard $ cap shared across processes, refused before the call. Resumes where it stopped | Done, ran 42 days |
| `dataset.py` | STaR filter. Keeps a day only if the final plan passed the rule checker, isn't the all-rest fallback, does at least 30% of the work, and ranks at least as high as every optimizer-only plan. Makes 3 chat examples per day: `plan`, `ideas`, `explain`. Split by scenario | Done, tested |
| `train.py` | Waits for the main ML session, builds the dataset, runs `mlx_lm lora` | Written, **never run** |
| `client.py` | `LocalLLM`: same interface as B's `LLM` (`.text`, `.create`, `.cost_usd`, `.calls`). $0 cost. No tool use (`supports_tools = False`) | Done, used by the base-model eval |
| `evaluate.py` | Held-out comparison on B's 12 test days: first try (no checker) and full pipeline (checker in the loop) | Done, base model scored |
| `test_local_planner.py` | 7 pytest tests (test set refused, budget cap, dataset filter, parsing) | Passing |

Two choices made along the way:
- The teacher skips Claude's critic step (the code critic still runs). It was half the cost per day, and its 9k-token prompts are too long for the small model. `LocalLLM` skips it the same way by default.
- The `ideas` examples teach the **verified** plan as `direct_plan`, never Claude's own schedule when it failed (STaR "rationalization": show the right answer).

## Teacher data collected

Stopped at 42 of 263 days by the freeze. Committed copy in `ml/local_planner/data/` (2.1 MB, synthetic days, no real health data). Working copy: `data/local_planner/` in the main checkout.

| | |
| --- | --- |
| Days run | 42 (14 B train, 14 new seeds, 14 F curriculum) |
| Agent's final plan passed the rule checker | 42 / 42 (100%) |
| Kept by the STaR filter | 39 / 42 (3 dropped: under 30% of the work done) |
| Claude's own direct schedule offered | 15 / 42 days (Claude often returns `direct_plan: null`) |
| ...and it passed the rule checker | 11 / 15 (73%). Failures: heat 4, lightning 1. Much better than Claude alone (8%), because here it sees the optimizer's best plan first |
| Teaching target | agent's final plan 37 days, Claude's own passing plan 2 days |
| Training examples | 117 train + 6 valid (plan / ideas / explain, 39 + 2 each), 309k tokens, none over 8,192 tokens |
| **API cost** | **$3.59** (86 Claude calls, about $0.085 per day). Cap was $30 |

## Held-out results so far (B's 12 test days)

**First try (no rule checker)**

| Planner | Runs | Valid | Pass on first try | Runs w/ safety violations | Throughput | Peak p90 (°C) | Latency (s) | Cost $/plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Opus 5.5 alone (B's `llm_only`) | 60 | 100% | 8% | 92% | 0.87 | 38.80 | 21.7 | 0.052 |
| Qwen3-1.7B base, no fine-tune | 60 | 0% | 0% | 3% | 0.00 | - | 6.1 | 0 |
| Qwen3-1.7B + LoRA | not trained yet | | | | | | | |

**Full planner (rule checker in the loop)**

| Planner | Runs | Safety violations after checker | Throughput | Peak p90 (°C) | Heat spread | Latency (s) | Cost $/plan |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Optimizer only (re-run here) | 12 | 0 | 0.68 | 38.00 | 31 | 12.7 | 0 |
| Claude agent (B's `agent_day1`) | 60 | 0 | 0.65 | 38.00 | 23 | 59.4 | 0.153 |
| Base Qwen, best of 5 + checker | 12 | 0 | 0.68 | 38.00 | 31 | 43.3 | 0 |

- The base model never writes a valid schedule (wrong number of slots, made-up structure), so best-of-5 always fell back to the optimizer. That's the floor the fine-tune has to beat.
- Safety violations after the checker are 0 for every row, as they must be.
- Our optimizer re-run gets 0.68 vs 0.64 in B's table: we cap CP-SAT at 2 threads and use a deterministic time budget so other sessions' CPU load can't change the plan.
- Not run yet: base and fine-tuned models inside the full agent pipeline.

## Steps to finish after the event

Run these from the repo root after merging this branch (the default paths then point at `data/local_planner/` and `ml/checkpoints/local_planner/`).

1. Check the CPU is free. This must print nothing:
   ```bash
   pgrep -f "ml.final|ml.train|ml.search|ml.meta"
   ```
2. If the base model is missing (`ml/checkpoints/local_planner/Qwen3-1.7B-4bit`), download it (0.98 GB):
   ```bash
   .venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/Qwen3-1.7B-4bit', local_dir='ml/checkpoints/local_planner/Qwen3-1.7B-4bit')"
   ```
3. If `data/local_planner/teacher_runs/` is missing, copy the committed runs there:
   ```bash
   mkdir -p data/local_planner && cp -R ml/local_planner/data/teacher_runs data/local_planner/
   ```
4. Optional, recommended: collect the other 221 days (about $19, about 2-3 hours). It skips days already done and stops at the cap:
   ```bash
   .venv/bin/python -u -m ml.local_planner.teacher --cap 30 --workers 3
   ```
5. Train the adapter (2 epochs; builds the dataset first):
   ```bash
   .venv/bin/python -u -m ml.local_planner.train
   ```
   If it runs out of memory, lower `MAX_TOKENS` in `train.py` to 6144 (drops the longest `ideas` examples).
6. Score the fine-tuned model on the held-out days, first try:
   ```bash
   .venv/bin/python -u -m ml.local_planner.evaluate --only first_try --models finetuned --runs 5
   ```
7. Score both local models inside the full agent pipeline:
   ```bash
   .venv/bin/python -u -m ml.local_planner.evaluate --only pipeline --models finetuned,base
   ```
   It prints the full table and saves `data/local_planner/eval/summary.json`. Paste the table here.
8. Run the tests:
   ```bash
   .venv/bin/python -m pytest ml/local_planner -q
   ```

One test to check training worked: in step 6, the fine-tuned model's "Valid" column should be well above the base model's 0%.

## Plugging it in (for B or the main session, not done: B owns `backend/planner/`)

1. In `backend/planner/llm.py`, make `get_llm()` return the local model when `.env` says `PLANNER_LLM=local`:
   ```python
   if os.getenv("PLANNER_LLM", "on").lower() == "local":
       from ml.local_planner.client import LocalLLM
       return LocalLLM()
   ```
   `llm_available()` should also return True in that mode (no API key needed).
2. In `backend/planner/agent.py` chat, treat `getattr(llm, "supports_tools", True) is False` like no-key mode (the local model can't call tools; the crew lead gets the verified-plan summary).
3. Nothing else changes: `plan_with_agent(..., llm=LocalLLM())` already works, and every plan still goes through `rules.py`.

## Decision so far

No decision yet. The fine-tuned model doesn't exist, so the question "does it beat Claude?" is still open. What we know: the base model is useless on its own (0% valid), and the optimizer alone already gets as much work done as the Claude agent on the test days. Until steps 5-7 are done, keep the current planner (optimizer + Claude agent, or optimizer only without a key). Don't ship `PLANNER_LLM=local`.
