"""
Train the forecaster: pretrain on the simulator, fine-tune on real people.

    1. Pretrain on simulated shifts (split by person: 90% train, 10% validation).
    2. Fine-tune on the Mendeley training people (lower learning rate),
       early-stopping on the Mendeley validation people.
    3. Repeat with different random seeds -> an ensemble.

Sensors are randomly hidden during training (skin temp, activity, weather, workload,
or everything except heart rate), so the model works when a sensor fails, and on the
Mendeley data, which only has heart rate.

Loss (per batch):
    forecast: pinball (quantile) loss at the 10th/50th/90th percentile, every minute 1..30 ahead
    now:      Gaussian negative log-likelihood (honest uncertainty)
              + safety penalty: underestimating is punished harder, 3x above 38 °C
    both:     smoothness penalty: the forecast can't change faster than 0.05 °C per minute

Run:   python -m ml.train --name v1 --seeds 1
Score: python -m ml.evaluate --model ml/checkpoints/v1.pt
"""

import argparse
import csv
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from backend.models.features import FUTURE_FEATURES, PAST_FEATURES, build_features, kalman_baseline
from backend.models.forecaster import HORIZON, QUANTILES, HeatForecaster, future_windows
from ml.data import Session, load_mendeley, load_split, make_person_split, sessions_in
from ml.evaluate import EVAL_START_MIN
from ml.simulate import load_sim

CKPT_DIR, RUN_DIR = Path("ml/checkpoints"), Path("ml/runs")

# Loss weights
W_NLL, W_SAFETY, W_SMOOTH = 0.3, 0.5, 1.0
SAFETY_HOT_C, SAFETY_HOT_WEIGHT = 38.0, 3.0
MAX_RATE_C_PER_MIN = 0.05

# Which feature columns belong to each hideable sensor
_SENSOR_COLS = {
    name: ([PAST_FEATURES.index(name)], [PAST_FEATURES.index(f"{name}_missing")],
           [FUTURE_FEATURES.index(name)] if name in FUTURE_FEATURES else [],
           [FUTURE_FEATURES.index(f"{name}_missing")] if name in FUTURE_FEATURES else [])
    for name in ("skin_temp", "activity", "wbgt", "workload")
}


@dataclass
class Tensors:
    """One session, ready for the network."""
    past: np.ndarray       # (n, n_past)
    future: np.ndarray     # (n, n_future)
    kalman: np.ndarray     # (n,) Kalman core temp
    target: np.ndarray     # (n,) true core temp (NaN where not measured)
    person_id: str


def to_tensors(s: Session) -> Tensors:
    past, future = build_features(s.signals)
    target = np.where(s.core_temp_measured, s.signals["core_temp"], np.nan).astype(np.float32)
    return Tensors(past, future, kalman_baseline(s.signals).astype(np.float32), target, s.person_id)


def make_batch(items: list[Tensors], rng: random.Random, k_times: int, hide_sensors: bool):
    """Pad sessions to the same length, optionally hide sensors, pick forecast times."""
    T = max(len(x.kalman) for x in items)
    B = len(items)
    past = np.zeros((B, T, len(PAST_FEATURES)), np.float32)
    future = np.zeros((B, T, len(FUTURE_FEATURES)), np.float32)
    kal = np.zeros((B, T), np.float32)
    tgt = np.full((B, T), np.nan, np.float32)
    times = np.zeros((B, k_times), np.int64)
    for b, x in enumerate(items):
        n = len(x.kalman)
        past[b, :n], future[b, :n], kal[b, :n], tgt[b, :n] = x.past, x.future, x.kalman, x.target
        if hide_sensors:
            hide_all = rng.random() < 0.2
            for p_val, p_miss, f_val, f_miss in _SENSOR_COLS.values():
                if hide_all or rng.random() < 0.3:
                    past[b, :, p_val], past[b, :, p_miss] = 0.0, 1.0
                    future[b, :, f_val], future[b, :, f_miss] = 0.0, 1.0
        times[b] = [rng.randrange(EVAL_START_MIN, n) for _ in range(k_times)]
    return (torch.from_numpy(past), torch.from_numpy(future), torch.from_numpy(kal),
            torch.from_numpy(tgt), torch.from_numpy(times))


def compute_loss(model: HeatForecaster, batch) -> tuple[torch.Tensor, dict]:
    past, future, kal, tgt, times = batch
    B, T = kal.shape
    h = model.encode(past)

    # --- NOW head, every measured minute from minute 5 on
    mu, logv = model.now(h)
    y_now = tgt - kal
    m = ~torch.isnan(y_now)
    m[:, :5] = False
    y_now = torch.nan_to_num(y_now)
    nll = (0.5 * (logv + (y_now - mu) ** 2 / logv.exp()))[m].mean()
    weight = 1 + (SAFETY_HOT_WEIGHT - 1) * (torch.nan_to_num(tgt) > SAFETY_HOT_C).float()
    safety = (weight * F.relu(y_now - mu) ** 2)[m].mean()

    # --- FORECAST head at K random minutes per session
    K = times.shape[1]
    b_idx = torch.arange(B).unsqueeze(1).expand(B, K)
    h_t = h[b_idx, times].reshape(B * K, -1)
    fut = future_windows(future, times).reshape(B * K, HORIZON, -1)
    fc = model.forecast(h_t, fut).view(B, K, HORIZON, 3)
    ahead = times.unsqueeze(-1) + torch.arange(1, HORIZON + 1)                  # (B, K, 30)
    inside = ahead < T
    y_f = torch.full((B, K, HORIZON), float("nan"))
    y_f[inside] = tgt[b_idx.unsqueeze(-1).expand_as(ahead)[inside], ahead[inside]]
    y_f = y_f - kal[b_idx, times].unsqueeze(-1)
    mf = ~torch.isnan(y_f)
    y_f = torch.nan_to_num(y_f)
    pin = 0.0
    for i, q in enumerate(QUANTILES):
        d = y_f - fc[..., i]
        pin = pin + torch.maximum(q * d, (q - 1) * d)[mf].mean()
    pin = pin / len(QUANTILES)

    # --- Smoothness: core temp can't jump. Penalize the median path moving faster than the limit,
    # including the step from "now" to 1 minute ahead.
    now_at_t = mu[b_idx, times].unsqueeze(-1)
    path = torch.cat([now_at_t, fc[..., 1]], dim=-1)
    smooth = (F.relu((path[..., 1:] - path[..., :-1]).abs() - MAX_RATE_C_PER_MIN) ** 2).mean()

    loss = pin + W_NLL * nll + W_SAFETY * safety + W_SMOOTH * smooth
    return loss, {"loss": loss.item(), "pinball": pin.item(), "nll": nll.item(),
                  "safety": safety.item(), "smooth": smooth.item()}


@torch.no_grad()
def evaluate_loss(model, items, seed=123, batch_size=64, k_times=16):
    model.eval()
    rng = random.Random(seed)            # same forecast times every epoch, so scores are comparable
    totals, n = {}, 0
    for i in range(0, len(items), batch_size):
        _, parts = compute_loss(model, make_batch(items[i:i + batch_size], rng, k_times, hide_sensors=False))
        for k, v in parts.items():
            totals[k] = totals.get(k, 0) + v
        n += 1
    model.train()
    return {k: v / n for k, v in totals.items()}


def train_stage(model, train_items, val_items, *, lr, max_epochs, patience, batch_size, k_times,
                seed, log_path, stage, hide_sensors=True):
    """Train with AdamW + gradient clipping, keep the weights with the best validation loss."""
    rng = random.Random(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    best, best_state, bad = float("inf"), None, 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not log_path.exists()
    with log_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["stage", "seed", "epoch", "train_loss", "val_loss", "val_pinball", "val_nll", "seconds"])
        for epoch in range(1, max_epochs + 1):
            t0 = time.time()
            order = train_items[:]
            rng.shuffle(order)
            losses = []
            for i in range(0, len(order), batch_size):
                loss, _ = compute_loss(model, make_batch(order[i:i + batch_size], rng, k_times, hide_sensors))
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                losses.append(loss.item())
            val = evaluate_loss(model, val_items)
            w.writerow([stage, seed, epoch, np.mean(losses), val["loss"], val["pinball"], val["nll"], round(time.time() - t0, 1)])
            f.flush()
            print(f"  [{stage} seed {seed}] epoch {epoch}: train {np.mean(losses):.4f}  val {val['loss']:.4f} "
                  f"(pinball {val['pinball']:.4f})  {time.time() - t0:.0f}s")
            if val["loss"] < best - 1e-4:
                best, best_state, bad = val["loss"], {k: v.clone() for k, v in model.state_dict().items()}, 0
            else:
                bad += 1
                if bad >= patience:
                    break
    model.load_state_dict(best_state)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="v1")
    ap.add_argument("--seeds", type=int, default=1, help="ensemble size")
    ap.add_argument("--pretrain-epochs", type=int, default=15)
    ap.add_argument("--finetune-epochs", type=int, default=100)
    ap.add_argument("--sim-limit", type=int, default=0, help="use only this many sim shifts (0 = all)")
    ap.add_argument("--no-finetune", action="store_true")
    args = ap.parse_args()
    torch.set_num_threads(os.cpu_count() or 4)

    print("Building features...")
    sim = load_sim()
    if args.sim_limit:
        sim = sim[:args.sim_limit]
    sim_split = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10, n_calib=0, n_test=0)
    sim_train = [to_tensors(s) for s in sessions_in(sim, sim_split["train"])]
    sim_val = [to_tensors(s) for s in sessions_in(sim, sim_split["val"])]
    real = load_mendeley()
    split = load_split("mendeley")
    real_train = [to_tensors(s) for s in sessions_in(real, split["train"])]
    real_val = [to_tensors(s) for s in sessions_in(real, split["val"])]
    print(f"sim: {len(sim_train)} train / {len(sim_val)} val shifts; real: {len(real_train)} train / {len(real_val)} val sessions")

    model_args = {"hidden": 64, "layers": 2, "dropout": 0.1}
    log_path = RUN_DIR / args.name / "log.csv"
    states = []
    for seed in range(args.seeds):
        torch.manual_seed(seed)
        model = HeatForecaster(**model_args)
        if seed == 0:
            print(f"Model: {sum(p.numel() for p in model.parameters()):,} parameters")
        train_stage(model, sim_train, sim_val, lr=1e-3, max_epochs=args.pretrain_epochs, patience=3,
                    batch_size=64, k_times=16, seed=seed, log_path=log_path, stage="pretrain")
        if not args.no_finetune:
            train_stage(model, real_train, real_val, lr=3e-4, max_epochs=args.finetune_epochs, patience=10,
                        batch_size=16, k_times=32, seed=seed, log_path=log_path, stage="finetune")
        states.append(model.state_dict())

    trained_on = set(sim_split["train"]) | (set() if args.no_finetune else set(split["train"]))
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    path = CKPT_DIR / f"{args.name}.pt"
    torch.save({"models": states, "model_args": model_args, "trained_on": sorted(trained_on), "name": args.name}, path)
    print(f"Saved {len(states)}-model ensemble to {path}")


if __name__ == "__main__":
    main()
