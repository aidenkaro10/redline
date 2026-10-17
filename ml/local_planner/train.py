"""
LoRA fine-tune of Qwen3-1.7B (4-bit) on the teacher data, with Apple's mlx-lm. Saves only the adapter.

Steps:
  1. Wait while the main ML session is training (pgrep ml.final / ml.train / ml.search / ml.meta).
  2. Build the chat dataset (dataset.py): train/valid split by scenario, long examples dropped.
  3. Run `python -m mlx_lm lora` with the settings below. Loss only on the answer (--mask-prompt).
     The adapter (~20 MB) goes to ml/checkpoints/local_planner/adapter. The base model is not changed.

Run:  python -m ml.local_planner.train            (add --no-wait to skip the CPU check)
"""

import argparse
import json
import math
import subprocess
import sys
import time

from ml.local_planner.common import ADAPTER_DIR, BASE_MODEL_DIR, DATA_DIR, out_dir
from ml.local_planner.dataset import SFT_DIR, build

BUSY_PATTERN = "ml.final|ml.train|ml.search|ml.meta"
MAX_TOKENS = 8192             # longest example we train on (the ideas prompts are ~4-7k tokens)

SETTINGS = {
    "fine_tune_type": "lora",
    "num_layers": -1,                     # LoRA on every layer
    "lora_parameters": {"rank": 16, "scale": 20.0, "dropout": 0.05},
    "batch_size": 1,                      # examples are long; 16 GB laptop
    "grad_accumulation_steps": 4,         # = an effective batch of 4
    "learning_rate": 1e-4,
    "mask_prompt": True,                  # learn the answers, not the prompts
    "max_seq_length": MAX_TOKENS,
    "grad_checkpoint": True,              # trades speed for memory
    "steps_per_report": 20,
    "steps_per_eval": 200,
    "val_batches": -1,                    # the whole validation set
    "save_every": 200,
    "seed": 0,
}


def busy() -> bool:
    return subprocess.run(["pgrep", "-f", BUSY_PATTERN], capture_output=True).returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    while not args.no_wait and busy():
        print(time.strftime("%H:%M"), "main ML session is training; waiting 2 min", flush=True)
        time.sleep(120)

    from transformers import AutoTokenizer
    stats = build(AutoTokenizer.from_pretrained(str(BASE_MODEL_DIR)), MAX_TOKENS)
    n_train = sum(v for k, v in stats.items() if k.startswith("train:"))
    print("dataset:", json.dumps(stats), flush=True)
    iters = math.ceil(args.epochs * n_train / SETTINGS["batch_size"])
    updates = iters // SETTINGS["grad_accumulation_steps"]   # the LR schedule ticks once per optimizer update
    cfg = {**SETTINGS, "model": str(BASE_MODEL_DIR), "train": True, "data": str(SFT_DIR),
           "adapter_path": str(out_dir(ADAPTER_DIR)), "iters": iters,
           "lr_schedule": {"name": "cosine_decay", "warmup": 10,
                           "arguments": [SETTINGS["learning_rate"], updates, SETTINGS["learning_rate"] / 10]}}
    cfg_path = DATA_DIR / "lora_config.yaml"
    cfg_path.write_text(json.dumps(cfg, indent=1))          # JSON is valid YAML
    print(f"training {iters} steps ({args.epochs} epochs of {n_train} examples)", flush=True)
    t0 = time.time()
    subprocess.run([sys.executable, "-m", "mlx_lm", "lora", "-c", str(cfg_path)], check=True)
    print(f"done in {(time.time() - t0) / 60:.0f} min. Adapter: {ADAPTER_DIR}", flush=True)


if __name__ == "__main__":
    main()
