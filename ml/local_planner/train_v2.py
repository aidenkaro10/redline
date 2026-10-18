"""
Retrain the LoRA on the EDIT format, keeping all three old jobs in the mix so it doesn't forget them.

Data (training days only):
  - edit examples: optimizer plan -> edits to the verified plan Claude's agent picked (edits.py), x2
  - replay of the original Claude-taught examples: every `plan` example, 100 `ideas`, 100 `explain`
  - optional good self-samples (edit answers the model wrote that passed the checker and beat the
    optimizer's plan score), from --self data/local_planner/self_edits.jsonl
Starts from the current adapter (same LoRA shape), one pass, lower learning rate.
Saves to ml/checkpoints/local_planner/adapter_edit (the current adapter is never overwritten).

Run:  python -m ml.local_planner.train_v2 [--name adapter_edit] [--self path.jsonl]
"""

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

from ml.local_planner.common import BASE_MODEL_DIR, CKPT_DIR, DATA_DIR, out_dir

from backend.planner.baselines import LLM_SYSTEM
from ml.local_planner.dataset import SFT_DIR
from ml.local_planner.edits import edit_examples

LR = 5e-5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="adapter_edit")
    ap.add_argument("--start", default="adapter")
    ap.add_argument("--self", dest="self_path", default="")
    ap.add_argument("--edit-copies", type=int, default=2)
    args = ap.parse_args()
    rng = random.Random(0)

    train_edits, st = edit_examples("train")
    valid_edits, _ = edit_examples("val")          # only for the validation LOSS, never trained on
    print("edit examples:", st, "valid:", len(valid_edits), flush=True)
    claude = [json.loads(l) for l in (SFT_DIR / "train.jsonl").read_text().splitlines()]
    plan = [e for e in claude if e["messages"][0]["content"] == LLM_SYSTEM]
    ideas = [e for e in claude if e["messages"][0]["role"] == "system" and e not in plan]
    explain = [e for e in claude if e["messages"][0]["role"] != "system"]
    mine = [json.loads(l) for l in Path(args.self_path).read_text().splitlines()] if args.self_path else []
    rows = train_edits * args.edit_copies + plan + rng.sample(ideas, min(100, len(ideas))) \
        + rng.sample(explain, min(100, len(explain))) + mine
    rng.shuffle(rows)
    data = out_dir(DATA_DIR / f"sft_{args.name}")
    (data / "train.jsonl").write_text("".join(json.dumps({"messages": r["messages"]}) + "\n" for r in rows))
    valid = [json.loads(l) for l in (SFT_DIR / "valid.jsonl").read_text().splitlines()] + valid_edits
    (data / "valid.jsonl").write_text("".join(json.dumps({"messages": r["messages"]}) + "\n" for r in valid))
    print(f"dataset: {len(rows)} train ({len(train_edits)}x{args.edit_copies} edits, {len(plan)} plan, "
          f"100 ideas, 100 explain, {len(mine)} self), {len(valid)} valid", flush=True)

    from ml.local_planner.train import SETTINGS
    iters = len(rows)
    updates = iters // SETTINGS["grad_accumulation_steps"]
    cfg = {**SETTINGS, "model": str(BASE_MODEL_DIR), "train": True, "data": str(data),
           "adapter_path": str(out_dir(CKPT_DIR / args.name)), "iters": iters, "learning_rate": LR,
           "resume_adapter_file": str(CKPT_DIR / args.start / "adapters.safetensors"),
           "max_seq_length": 4096, "steps_per_eval": 200, "val_batches": 40, "save_every": 200,
           "lr_schedule": {"name": "cosine_decay", "warmup": 10, "arguments": [LR, updates, LR / 10]}}
    cfg_path = data / "lora_config.yaml"
    cfg_path.write_text(json.dumps(cfg, indent=1))
    subprocess.run([sys.executable, "-m", "mlx_lm", "lora", "-c", str(cfg_path)], check=True)


if __name__ == "__main__":
    main()
