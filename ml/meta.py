"""
Meta-learned twins (CAVIA, Zintgraf et al. 2019): train the network so that learning
a twin from HEART RATE actually improves CORE TEMP forecasts.

Why: twins v1 (ml/twins.py) got better at heart rate but worse at core temp on real
people (docs/results.md). The thing we could check (HR) drifted from the goal (core
temp). CAVIA fixes the alignment during training, where the truth is known:

    for a batch of people:
        twin = zeros                                   (the average person)
        repeat K_INNER times:                          (inner loop, only the twin moves)
            twin -= LR_INNER * grad of HEART RATE loss on day A
        outer loss = full CORE TEMP loss on day B using that twin
        update the NETWORK on the outer loss, backpropagating through the inner steps

So the network learns a twin space where "fit heart rate" means "fit this body".
Live, the exact same K_INNER steps run on the worker's heart rate. Nothing else moves.

Stages:  pretrain on the simulator (two different days per person), then fine-tune on
real people (adapt on one session, score the other, both directions), always mixed
with ordinary average-person batches so brand-new workers stay good.

Run:   python -m ml.meta --name meta4
Proof: python -m ml.meta --proof ml/checkpoints/meta4.pt
"""

import argparse
import os
import random
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from backend.models.forecaster import HORIZON, ForecasterPredictor, HeatForecaster, future_windows
from ml.data import load_mendeley, load_split, make_person_split, sessions_in
from ml.evaluate import EVAL_START_MIN, evaluate, print_reports
from ml.simulate import load_sim
from ml.train import CKPT_DIR, RUN_DIR, compute_loss, evaluate_loss, hr_targets, make_batch, to_tensors

TWIN_DIM = 4
K_INNER = 3           # adaptation steps (same number live)
LR_INNER = 1.0        # inner step size on the twin
INNER_BETA = 0.01     # pull toward the average person during adaptation
TWIN_CAP = 1.5        # max distance from the average person (applied live)
META_SHARE = 0.5      # share of batches that are meta batches (the rest: average person)
HR_TIMES = 16


def hr_loss_rows(model, batch, twins, rng):
    """Mean heart-rate-head error per row (B,), for random forecast minutes."""
    past, future, _, _, _, hr = batch
    B, T = past.shape[:2]
    times = torch.tensor([[rng.randrange(EVAL_START_MIN, max(EVAL_START_MIN + 1, T - 11)) for _ in range(HR_TIMES)]
                          for _ in range(B)])
    h = model.encode(past, twins)
    b_idx = torch.arange(B).unsqueeze(1).expand(B, HR_TIMES)
    h_t = h[b_idx, times].reshape(B * HR_TIMES, -1)
    fut = future_windows(future, times).reshape(B * HR_TIMES, HORIZON, -1)
    _, hr_pred = model.forecast_full(h_t, fut, twins.repeat_interleave(HR_TIMES, dim=0))
    y = hr_targets(past, hr, times)
    m = (~torch.isnan(y)).float()
    err = (hr_pred.view(B, HR_TIMES, -1) - torch.nan_to_num(y)) ** 2 * m
    return err.sum((1, 2)) / m.sum((1, 2)).clamp(min=1)


def adapt(model, batch, rng, create_graph: bool) -> torch.Tensor:
    """K_INNER gradient steps on each row's twin, from heart rate only. Returns (B, TWIN_DIM)."""
    B = batch[0].shape[0]
    twins = torch.zeros(B, model.twin_dim, requires_grad=True)
    for _ in range(K_INNER):
        loss = (hr_loss_rows(model, batch, twins, rng) + INNER_BETA * (twins ** 2).sum(1)).sum()
        (g,) = torch.autograd.grad(loss, twins, create_graph=create_graph)
        twins = twins - LR_INNER * g
    return twins


def capped(twin: torch.Tensor) -> torch.Tensor:
    norm = twin.norm(dim=-1, keepdim=True).clamp(min=1e-8)
    return twin * torch.clamp(TWIN_CAP / norm, max=1.0)


def pairs_by_person(items):
    by = defaultdict(list)
    for x in items:
        by[x.person_id].append(x)
    return {p: xs for p, xs in by.items() if len(xs) >= 2}


def meta_stage(model, train_items, val_items, *, lr, epochs, batches_per_epoch, batch_size, k_times, seed, stage, log):
    rng = random.Random(seed)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    pairs = pairs_by_person(train_items)
    people = list(pairs)
    best, best_state = float("inf"), None
    for epoch in range(1, epochs + 1):
        t0, losses = time.time(), []
        for _ in range(batches_per_epoch):
            if rng.random() < META_SHARE:
                chosen = [rng.sample(pairs[p], 2) for p in rng.sample(people, min(batch_size, len(people)))]
                day_a = make_batch([a for a, _ in chosen], rng, k_times, hide_sensors=True)
                day_b = make_batch([b for _, b in chosen], rng, k_times, hide_sensors=True)
                twins = adapt(model, day_a, rng, create_graph=True)
                loss, _ = compute_loss(model, day_b, twins)
            else:
                loss, _ = compute_loss(model, make_batch(rng.sample(train_items, batch_size), rng, k_times, True))
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            losses.append(loss.item())
        val = meta_val(model, val_items, seed)
        print(f"  [{stage}] epoch {epoch}: train {np.mean(losses):.4f}  val cold {val['cold']:.4f}  "
              f"val adapted {val['adapted']:.4f}  {time.time() - t0:.0f}s", flush=True)
        log.write(f"{stage},{epoch},{np.mean(losses):.5f},{val['cold']:.5f},{val['adapted']:.5f}\n")
        log.flush()
        score = val["cold"] + val["adapted"]          # good for new workers AND after adapting
        if score < best - 1e-4:
            best, best_state = score, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)


def meta_val(model, items, seed, batch_size=16, k_times=16):
    """Validation loss for brand-new people (cold) and after adapting on another day (adapted)."""
    model.eval()
    rng = random.Random(seed + 999)
    pairs = pairs_by_person(items)
    cold, adapted, n = 0.0, 0.0, 0
    people = sorted(pairs)
    for i in range(0, len(people), batch_size):
        chosen = [pairs[p][:2] for p in people[i:i + batch_size]]
        day_a = make_batch([a for a, _ in chosen], rng, k_times, False)
        day_b = make_batch([b for _, b in chosen], rng, k_times, False)
        with torch.no_grad():
            cold += compute_loss(model, day_b)[0].item()
        twins = capped(adapt(model, day_a, rng, create_graph=False)).detach()
        with torch.no_grad():
            adapted += compute_loss(model, day_b, twins)[0].item()
        n += 1
    model.train()
    return {"cold": cold / n, "adapted": adapted / n}


def adapt_live(model: HeatForecaster, sessions, seed: int = 0) -> np.ndarray:
    """What the live system runs after a shift: the same K_INNER steps, heart rate only, capped."""
    items = [to_tensors(s) for s in sessions]
    rng = random.Random(seed)
    batch = make_batch(items, rng, k_times=1, hide_sensors=False)
    model.eval()
    twins = adapt(model, batch, rng, create_graph=False)       # one twin per session...
    return capped(twins.mean(0)).detach().numpy()              # ...averaged into the person's twin


def proof(path: str) -> None:
    """Unseen real people: adapt on the neutral session's HR, score the heat session (and the reverse)."""
    from ml.twins import _with_twins
    pred = ForecasterPredictor.load(path, calibrated=False)
    split = load_split("mendeley")
    data = sessions_in(load_mendeley(), split["val"] + split["calib"])
    by = defaultdict(dict)
    for s in data:
        by[s.person_id][s.condition] = s
    base, adapted = [], []
    for pid, sess in sorted(by.items()):
        if "neutral" in sess and "heat" in sess:
            for a, b in (("neutral", "heat"), ("heat", "neutral")):
                twins = [adapt_live(m, [sess[a]]) for m in pred.models]
                base.append(sess[b])
                adapted.append(_with_twins([sess[b]], twins)[0])
    print_reports([evaluate("average person", pred, base, trained_on=pred.trained_on),
                   evaluate("meta twin", pred, adapted, trained_on=pred.trained_on)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="meta4")
    ap.add_argument("--proof", default="")
    ap.add_argument("--pretrain-epochs", type=int, default=8)
    ap.add_argument("--batches", type=int, default=150, help="batches per pretrain epoch")
    ap.add_argument("--sim", default="data/sim/sim_v2.npz")
    ap.add_argument("--sim-limit", type=int, default=0)
    ap.add_argument("--finetune-epochs", type=int, default=30)
    args = ap.parse_args()
    torch.set_num_threads(6)
    if args.proof:
        return proof(args.proof)

    sim = load_sim(Path(args.sim))
    if args.sim_limit:
        sim = sim[:args.sim_limit]
    sim_split = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10, n_calib=0, n_test=0)
    sim_train = [to_tensors(s) for s in sessions_in(sim, sim_split["train"])]
    sim_val = [to_tensors(s) for s in sessions_in(sim, sim_split["val"])]
    real = load_mendeley()
    split = load_split("mendeley")
    real_train = [to_tensors(s) for s in sessions_in(real, split["train"])]
    real_val = [to_tensors(s) for s in sessions_in(real, split["val"])]
    print(f"sim {len(sim_train)}/{len(sim_val)} shifts, real {len(real_train)}/{len(real_val)} sessions", flush=True)

    torch.manual_seed(0)
    model_args = {"hidden": 64, "layers": 2, "dropout": 0.1, "twin_dim": TWIN_DIM, "hr_head": True}
    model = HeatForecaster(**model_args)
    (RUN_DIR / args.name).mkdir(parents=True, exist_ok=True)
    with open(RUN_DIR / args.name / "log.csv", "w") as log:
        log.write("stage,epoch,train,val_cold,val_adapted\n")
        meta_stage(model, sim_train, sim_val, lr=1e-3, epochs=args.pretrain_epochs, batches_per_epoch=args.batches,
                   batch_size=32, k_times=16, seed=0, stage="pretrain", log=log)
        meta_stage(model, real_train, real_val, lr=3e-4, epochs=args.finetune_epochs, batches_per_epoch=10,
                   batch_size=16, k_times=32, seed=0, stage="finetune", log=log)
    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    path = CKPT_DIR / f"{args.name}.pt"
    torch.save({"models": [model.state_dict()], "model_args": model_args, "name": args.name,
                "trained_on": sorted(set(sim_split["train"]) | set(split["train"])),
                "meta": {"k_inner": K_INNER, "lr_inner": LR_INNER, "beta": INNER_BETA, "cap": TWIN_CAP}}, path)
    print(f"Saved {path}", flush=True)


if __name__ == "__main__":
    main()
