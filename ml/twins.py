"""
Personal twins: learn a NEW worker's 16-number fingerprint from their heart rate alone.

Live, we never measure core temperature. We do see heart rate. So:
  1. Start the worker at the average person (all zeros).
  2. After a shift, freeze the whole network and adjust ONLY their 16 numbers so the
     heart rate head predicts their next 10 minutes of heart rate better.
     Loss = heart rate error + beta * (distance from the average person)^2   (spec formula)
  3. Gate: adapt on the first 80% of the minutes, then check the last 20%. Keep the new
     twin only if it predicts that unseen part better. Otherwise roll back to the old one.
  4. Cap how far a twin can move, so one weird shift can't push it somewhere unsafe.

The twin never sees core temperature, and it never changes any safety threshold.

Proof (the thing judges care about): does a twin learned from HEART RATE make the
CORE TEMP forecasts better?
  sim:  unseen virtual people, adapt on their first 3 work days, score the later days
  real: Mendeley people the network never trained on, adapt on their neutral session,
        score their heat session

Run:  python -m ml.twins --model ml/checkpoints/twins16.pt --proof real
      python -m ml.twins --model ml/checkpoints/twins16.pt --proof sim
"""

import argparse
import random
from dataclasses import dataclass

import numpy as np
import torch

from backend.models.forecaster import HORIZON, ForecasterPredictor, HeatForecaster, future_windows
from ml.data import Session, load_mendeley, load_split, make_person_split, sessions_in
from ml.evaluate import EVAL_START_MIN, evaluate, print_reports
from ml.train import hr_targets, make_batch, to_tensors

TWIN_BETA = 0.01          # pull toward the average person; bigger = more cautious adaptation
TWIN_MAX_NORM = 1.5       # hard cap on how far a twin can move from the average person
ADAPT_STEPS = 80
ADAPT_LR = 0.05
GATE_FRACTION = 0.2       # last 20% of each session's minutes are held out for the gate
GATE_MIN_GAIN = 0.02      # keep a new twin only if held-out HR error drops at least 2%


@dataclass
class AdaptResult:
    twin: np.ndarray       # the twin to use from now on
    kept: bool             # did the gate accept the update?
    hr_loss_before: float  # held-out HR error with the old twin
    hr_loss_after: float   # held-out HR error with the new twin


def _hr_loss(model: HeatForecaster, batch, twin: torch.Tensor, times: torch.Tensor) -> torch.Tensor:
    """Heart-rate-head error at the given forecast minutes (core temp is never used)."""
    past, future, _, _, _, hr = batch
    B = past.shape[0]
    K = times.shape[1]
    tw = twin.view(1, -1).expand(B, -1)
    h = model.encode(past, tw)
    b_idx = torch.arange(B).unsqueeze(1).expand(B, K)
    h_t = h[b_idx, times].reshape(B * K, -1)
    fut = future_windows(future, times).reshape(B * K, HORIZON, -1)
    _, hr_pred = model.forecast_full(h_t, fut, tw.repeat_interleave(K, dim=0))
    y = hr_targets(past, hr, times)
    m = ~torch.isnan(y)
    return ((hr_pred.view(B, K, -1) - torch.nan_to_num(y)) ** 2)[m].mean()


def _split_times(lengths: list[int], K: int):
    """Forecast minutes for adapting (early part) and for the gate (late part), padded per row."""
    fit, gate = [], []
    for n in lengths:
        cut = int(n * (1 - GATE_FRACTION))
        early = np.linspace(EVAL_START_MIN, max(EVAL_START_MIN + 1, cut - 11), K).astype(int)
        late = np.linspace(cut, max(cut + 1, n - 11), K).astype(int)
        fit.append(early)
        gate.append(late)
    return torch.tensor(np.array(fit)), torch.tensor(np.array(gate))


def adapt_twin(model: HeatForecaster, sessions: list[Session], start: np.ndarray | None = None,
               steps: int = ADAPT_STEPS) -> AdaptResult:
    """Learn a twin for one person from their sessions' heart rate. The network stays frozen."""
    assert model.twin_dim and model.hr_head, "needs a model trained with --twin-dim and --hr-head"
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    items = [to_tensors(s) for s in sessions]
    batch = make_batch(items, random.Random(0), k_times=1, hide_sensors=False)
    fit_t, gate_t = _split_times([len(x.kalman) for x in items], K=24)

    old = torch.zeros(model.twin_dim) if start is None else torch.as_tensor(start, dtype=torch.float32)
    twin = old.clone().requires_grad_(True)
    opt = torch.optim.Adam([twin], lr=ADAPT_LR)
    for _ in range(steps):
        loss = _hr_loss(model, batch, twin, fit_t) + TWIN_BETA * (twin ** 2).sum()
        opt.zero_grad()
        loss.backward()
        opt.step()
        with torch.no_grad():                       # the cap: never move too far from average
            norm = twin.norm()
            if norm > TWIN_MAX_NORM:
                twin.mul_(TWIN_MAX_NORM / norm)

    with torch.no_grad():
        before = _hr_loss(model, batch, old, gate_t).item()
        after = _hr_loss(model, batch, twin, gate_t).item()
    for p in model.parameters():
        p.requires_grad_(True)
    kept = after < before * (1 - GATE_MIN_GAIN)
    return AdaptResult(twin.detach().numpy() if kept else old.numpy(), kept, before, after)


# ---------------------------------------------------------------------------
# Proofs
# ---------------------------------------------------------------------------
def _with_twins(sessions: list[Session], twins: list) -> list[Session]:
    out = []
    for s in sessions:
        out.append(Session(s.person_id, s.session_id, s.source, s.condition, dict(s.signals),
                           s.core_temp_measured, {**s.meta, "twins": twins}))
    return out


def proof_real(path: str) -> None:
    """Adapt on each unseen Mendeley person's neutral session, score their heat session."""
    pred = ForecasterPredictor.load(path, calibrated=False)
    model = pred.models[0]
    split = load_split("mendeley")
    people = split["val"] + split["calib"]               # 20 people the network never trained on
    data = sessions_in(load_mendeley(), people)
    by_person = {}
    for s in data:
        by_person.setdefault(s.person_id, {})[s.condition] = s
    base, adapted, kept = [], [], 0
    for pid, sess in sorted(by_person.items()):
        if "neutral" not in sess or "heat" not in sess:
            continue
        r = adapt_twin(model, [sess["neutral"]])
        kept += r.kept
        base.append(sess["heat"])
        adapted.append(_with_twins([sess["heat"]], [r.twin])[0])
        print(f"  {pid}: HR error {r.hr_loss_before:.3f} -> {r.hr_loss_after:.3f}  {'KEPT' if r.kept else 'rolled back'}")
    print(f"Gate kept {kept} of {len(base)} twins.")
    print_reports([evaluate("average person", pred, base, trained_on=pred.trained_on),
                   evaluate("personal twin", pred, adapted, trained_on=pred.trained_on)])


def proof_sim(path: str, n_people: int = 60, adapt_days: int = 3) -> None:
    """Adapt on each unseen virtual person's first work days, score their later days."""
    from ml.simulate import load_sim
    pred = ForecasterPredictor.load(path, calibrated=False)
    model = pred.models[0]
    sim = load_sim()
    split = make_person_split([s.person_id for s in sim], n_val=len({s.person_id for s in sim}) // 10, n_calib=0, n_test=0)
    people = split["val"][:n_people]                     # same split as training: never trained on
    base, adapted, kept = [], [], 0
    for pid in people:
        days = sorted(sessions_in(sim, [pid]), key=lambda s: s.meta["day"])
        early, later = days[:adapt_days], days[adapt_days:]
        r = adapt_twin(model, early)
        kept += r.kept
        base += later
        adapted += _with_twins(later, [r.twin])
    print(f"Gate kept {kept} of {len(people)} twins.")
    print_reports([evaluate("average person", pred, base, trained_on=pred.trained_on),
                   evaluate("personal twin", pred, adapted, trained_on=pred.trained_on)])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--proof", choices=["real", "sim"], required=True)
    args = ap.parse_args()
    torch.set_num_threads(4)
    (proof_real if args.proof == "real" else proof_sim)(args.model)
