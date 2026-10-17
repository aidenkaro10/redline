"""
Why is the forecast what it is? One plain-English line, backed by numbers.

How it works (integrated gradients, in plain words):
  1. Build a "resting reference" shift: same length, same gaps, but a calm worker
     (65 bpm, comfortable weather, light work).
  2. Slide the real inputs from the reference to the real values in 32 small steps.
     At every step, ask the network "if this input nudged up, how much would the
     20-minute 90th-percentile forecast move?" (that is the gradient).
  3. Add the answers up. Each input column gets a share of the total difference
     between the real forecast and the reference forecast. Shares are in °C and
     add up to that difference (the "completeness" property, which we test).
  4. Pool the columns into a few groups people understand (heart rate, body heat,
     weather, workload, ...) and write a sentence about the biggest ones.

What is explained: how far the ensemble-average 90th-percentile forecast for 20 minutes
ahead sits above the current estimate ("p90 rise"). Subtracting "now" cancels the
Kalman estimate, so the shares describe what drives the CHANGE, not just "you are
already hot". Future weather and planned workload (known-future inputs) are included
in their groups. It does not include the ensemble-spread and conformal offsets added on top, which
are constants that do not depend on the inputs.

Use:
    from ml.explain.explain import explain
    out = explain(signals, minute, predictor)       # predictor = ForecasterPredictor
    out["why"]            # "Rising because heart rate jumped 18 bpm in 10 min and it's 31 °C WBGT."
    out["contributions"]  # {"heart_rate": +0.21, "body_heat": +0.05, ...}  in °C

See docs/interfaces/ml-explain.md.
"""

import numpy as np
import torch

from backend.models.features import FUTURE_FEATURES, PAST_FEATURES, build_features
from backend.models.forecaster import ForecasterPredictor, future_windows

torch.set_num_threads(2)

HORIZON_MIN = 20          # which forecast to explain (the yellow-alert horizon)
QUANTILE_INDEX = 2        # 0 = 10th, 1 = 50th, 2 = 90th percentile
STEPS = 32                # integration steps (more = more exact, slower)

# The "calm worker" (who has also just started the shift) the real session is compared against. Each value is used
# only at minutes where the real signal was measured; gaps stay gaps.
REFERENCE = {"hr": 65.0, "skin_temp": 33.5, "activity": 0.0, "wbgt": 20.0, "workload": 60.0}

# Which input columns belong to which human-friendly group.
GROUPS = {
    "heart_rate": ("hr", "hr_missing", "hr_delta5"),
    "body_heat": ("kalman_ct", "kalman_sd", "kalman_slope"),
    "skin_temp": ("skin_temp", "skin_temp_missing"),
    "activity": ("activity", "activity_missing"),
    "wbgt": ("wbgt", "wbgt_missing"),
    "workload": ("workload", "workload_missing"),
    "time": ("time_hr",),
}
GROUP_NAMES = tuple(GROUPS)

# Words used in the sentence only when a group is a top driver.
RISE_BAND_C = 0.15        # forecast at least this far above/below "now" counts as rising/falling
MIN_SHARE = 0.25          # a driver must carry at least 25% of the biggest driver's push
MAX_REASONS = 3
STEADY_PUSH_C = 0.05       # a steady forecast only names a driver if it pushes at least this much


def _reference_signals(signals: dict[str, np.ndarray], n: int) -> dict[str, np.ndarray]:
    """A calm shift with exactly the same gaps as the real one."""
    ref = {}
    for name, value in REFERENCE.items():
        real = np.asarray(signals.get(name, np.full(n, np.nan)), dtype=float)[:n]
        ref[name] = np.where(np.isnan(real), np.nan, value)
    return ref


def _padded(signals: dict, n: int) -> dict[str, np.ndarray]:
    """Cut every signal to n minutes (n may run past the end: pad with NaN = unknown)."""
    out = {}
    for k, v in signals.items():
        v = np.asarray(v, dtype=float)
        out[k] = np.concatenate([v[:n], np.full(max(0, n - len(v)), np.nan)])
    return out


def _inputs(signals: dict, minute: int):
    """
    Model inputs for the forecast made at `minute`:
      past   (minute + 1, n_past)   what the encoder reads
      future (30, n_future)         known-future weather / planned work for minutes+1..+30
    Rows past the end of the signals count as unknown (flagged missing).
    """
    n = minute + 31
    sig = _padded(signals, n)
    if "hr" not in sig:
        raise ValueError("signals needs at least 'hr'")
    # Only data up to `minute` may be used as the past; future wbgt/workload may extend 30 min on.
    past_sig = {k: v.copy() for k, v in sig.items()}
    for k in ("hr", "skin_temp", "activity"):
        if k in past_sig:
            past_sig[k][minute + 1:] = np.nan
    past, future = build_features(past_sig)
    f = torch.from_numpy(future).unsqueeze(0)
    window = future_windows(f, torch.tensor([[minute]]))[0, 0]          # (30, n_future)
    return past[:minute + 1], window.numpy(), sig


def _p90_delta(models, past: torch.Tensor, window: torch.Tensor) -> torch.Tensor:
    """Ensemble-mean 20-min 90th percentile (change from Kalman, °C) for a batch of inputs."""
    outs = []
    for m in models:
        h = m.encode(past)[:, -1]                                          # memory at the last minute
        fc = m.forecast(h, window)                                         # (B, 30, 3)
        now_delta = m.now(h[:, None])[0][:, 0]                             # NOW correction of the Kalman estimate
        # forecast minus now: the Kalman estimate cancels, so this is "how far above today's
        # estimate the cautious forecast sits"
        outs.append(fc[:, HORIZON_MIN - 1, QUANTILE_INDEX] - now_delta)
    return torch.stack(outs).mean(dim=0)


def integrated_gradients(models, past_x, window_x, past_b, window_b, steps: int = STEPS):
    """
    Returns (attr_past (T, n_past), attr_future (30, n_future), f_x, f_b).
    attr sums to f_x - f_b (up to integration error).
    """
    px, pb = torch.as_tensor(past_x), torch.as_tensor(past_b)
    wx, wb = torch.as_tensor(window_x), torch.as_tensor(window_b)
    alpha = ((torch.arange(steps) + 0.5) / steps).view(-1, 1, 1)           # midpoints of the path
    path_p = (pb + alpha * (px - pb)).requires_grad_(True)
    path_w = (wb + alpha * (wx - wb)).requires_grad_(True)
    f = _p90_delta(models, path_p, path_w)
    gp, gw = torch.autograd.grad(f.sum(), [path_p, path_w])
    attr_p = (gp.mean(0) * (px - pb)).detach().numpy()
    attr_w = (gw.mean(0) * (wx - wb)).detach().numpy()
    with torch.no_grad():
        both = _p90_delta(models, torch.stack([px, pb]), torch.stack([wx, wb]))
    return attr_p, attr_w, float(both[0]), float(both[1])


def group_contributions(attr_past: np.ndarray, attr_future: np.ndarray) -> dict[str, float]:
    """Pool column attributions (summed over time) into the human groups."""
    totals = {}
    for g, cols in GROUPS.items():
        total = 0.0
        for c in cols:
            if c in PAST_FEATURES:
                total += attr_past[:, PAST_FEATURES.index(c)].sum()
            if c in FUTURE_FEATURES:
                total += attr_future[:, FUTURE_FEATURES.index(c)].sum()
        totals[g] = float(total)
    return totals


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------
def _last_known(x: np.ndarray, t: int) -> float:
    """Most recent non-NaN value at or before minute t (NaN if none)."""
    seen = np.flatnonzero(~np.isnan(x[:t + 1]))
    return float(x[seen[-1]]) if len(seen) else float("nan")


def _facts(sig: dict, kalman_ct: np.ndarray, t: int) -> dict:
    hr_now = _last_known(sig["hr"], t)
    hr_then = _last_known(sig["hr"], max(0, t - 10))
    return {
        "hr": hr_now,
        "hr_jump": hr_now - hr_then,
        "core": float(kalman_ct[t]),
        "core_climb": float(kalman_ct[t] - kalman_ct[max(0, t - 10)]),
        "wbgt": _last_known(sig.get("wbgt", np.full(t + 1, np.nan)), t + 30) if "wbgt" in sig else float("nan"),
        "workload": _last_known(sig.get("workload", np.full(t + 1, np.nan)), t + 30) if "workload" in sig else float("nan"),
        "skin": _last_known(sig.get("skin_temp", np.full(t + 1, np.nan)), t),
        "activity": _last_known(sig.get("activity", np.full(t + 1, np.nan)), t),
        "minutes": t,
    }


def _work_word(w: float) -> str:
    if w < 150:
        return "light work"
    if w < 300:
        return "moderate work"
    if w < 450:
        return "hard work"
    return "very hard work"


def _phrase(group: str, push: float, f: dict) -> str | None:
    """A short fragment for one driver. push > 0 means it raises the forecast."""
    up = push > 0
    if group == "heart_rate" and not np.isnan(f["hr"]):
        if up and f["hr_jump"] >= 8:
            return f"heart rate jumped {f['hr_jump']:.0f} bpm in 10 min"
        if not up and f["hr_jump"] <= -8:
            return f"heart rate dropped {-f['hr_jump']:.0f} bpm in 10 min"
        word = "high at " if up and f["hr"] >= 110 else "low at " if not up and f["hr"] < 90 else ""
        return f"heart rate is {word}{f['hr']:.0f} bpm"
    if group == "body_heat":
        if up and f["core_climb"] >= 0.15:
            return f"body temperature climbed {f['core_climb']:.1f} °C in 10 min"
        return f"estimated core temp is {'already ' if up else 'steady at '}{f['core']:.1f} °C"
    if group == "wbgt" and not np.isnan(f["wbgt"]):
        return f"it's {f['wbgt']:.0f} °C WBGT" if up else f"it's only {f['wbgt']:.0f} °C WBGT"
    if group == "workload" and not np.isnan(f["workload"]):
        return _work_word(f["workload"]) if up else "light work"
    if group == "skin_temp" and not np.isnan(f["skin"]):
        return f"skin temp is {'high' if up else 'low'} at {f['skin']:.1f} °C"
    if group == "activity" and not np.isnan(f["activity"]):
        return "lots of movement" if up else "very little movement"
    if group == "time":
        if not up:
            return "early in the shift"
        m = f["minutes"]
        return f"{m} min into the shift" if m < 120 else f"{m / 60:.1f} h into the shift"
    return None


def make_why(direction: str, contributions: dict[str, float], facts: dict, now_c: float | None = None) -> str:
    """One sentence from the top drivers. Drivers must push the same way as the forecast."""
    sign = -1 if direction == "falling" else 1          # steady: report the biggest upward pressure
    ranked = sorted(contributions.items(), key=lambda kv: -sign * kv[1])
    top = sign * ranked[0][1]
    parts = []
    for g, c in ranked:
        if sign * c <= 0 or sign * c < MIN_SHARE * top or len(parts) >= MAX_REASONS:
            break
        p = _phrase(g, c, facts)
        if p:
            parts.append(p)
    joined = ""
    if parts:
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    if direction == "steady":
        near = f" near {now_c:.1f} °C" if now_c is not None else ""
        if joined and top >= STEADY_PUSH_C:
            return f"Holding steady{near}. Biggest upward pressure: {joined}."
        return f"Holding steady{near}. Nothing is pushing the forecast up or down."
    if not parts:
        return f"{direction.capitalize()}, no single clear driver."
    return f"{direction.capitalize()} because {joined}."


# ---------------------------------------------------------------------------
# The one function the live system calls
# ---------------------------------------------------------------------------
def explain(signals: dict[str, np.ndarray], minute: int, predictor: ForecasterPredictor, steps: int = STEPS) -> dict:
    """
    Explain the 20-minute 90th-percentile forecast made at `minute`.

    signals:   1-minute arrays, same dict you give predictor.predict_signals
               (hr, and optionally skin_temp, activity, wbgt, workload; NaN = not measured).
               wbgt / workload values for the 30 minutes after `minute` are read as the
               known-future weather and planned work (leave NaN if unknown).
    minute:    index of the minute to explain (0-based)
    predictor: a loaded ForecasterPredictor (the ensemble is explained as a whole)

    Returns a dict:
      why:            one plain-English sentence
      direction:      "rising" | "steady" | "falling" (median 20-min forecast vs the current estimate)
      contributions:  {group: °C}; positive pushes the forecast up. They add up to
                      p90_rise_c - reference_p90_rise_c (convergence_error_c is the leftover)
      now_c:          current estimate (Kalman + ensemble NOW correction)
      forecast_p90_c: ensemble 20-min 90th percentile (before spread/conformal offsets)
      median_rise_c:  median 20-min forecast minus now_c
      p90_rise_c:     90th-percentile 20-min forecast minus now_c (this is what is explained)
      reference_p90_rise_c: the same number for the calm reference worker
    """
    minute = int(minute)
    past_x, window_x, sig = _inputs(signals, minute)
    ref_sig = _reference_signals(sig, len(sig["hr"]))
    # the reference keeps every real signal's gaps; future wbgt / workload get the same treatment
    ref_full = {**{k: np.asarray(v, dtype=float) for k, v in sig.items()}, **ref_sig}
    past_b, window_b, _ = _inputs(ref_full, minute)
    past_b[:, PAST_FEATURES.index("time_hr")] = 0.0     # the reference worker has just started

    attr_p, attr_w, f_x, f_b = integrated_gradients(predictor.models, past_x, window_x, past_b, window_b, steps)
    contributions = group_contributions(attr_p, attr_w)

    # Where we are now and where the median is heading (for the direction word).
    kalman_ct = past_x[:, PAST_FEATURES.index("kalman_ct")] * 0.5 + 37.5
    with torch.no_grad():
        px, wx = torch.as_tensor(past_x).unsqueeze(0), torch.as_tensor(window_x).unsqueeze(0)
        now_delta, median_delta = [], []
        for m in predictor.models:
            h = m.encode(px)[:, -1]
            now_delta.append(m.now(h[:, None])[0][0, 0].item())
            median_delta.append(m.forecast(h, wx)[0, HORIZON_MIN - 1, 1].item())
    now_c = float(kalman_ct[minute] + np.mean(now_delta))
    median_c = float(kalman_ct[minute] + np.mean(median_delta))
    forecast_c = now_c + f_x                       # f_x = 90th pct minus NOW correction
    rise = median_c - now_c
    direction = "rising" if rise >= RISE_BAND_C else "falling" if rise <= -RISE_BAND_C else "steady"

    facts = _facts(sig, kalman_ct, minute)
    return {
        "why": make_why(direction, contributions, facts, now_c),
        "direction": direction,
        "contributions": {g: round(c, 4) for g, c in contributions.items()},
        "now_c": round(now_c, 3),
        "forecast_p90_c": round(forecast_c, 3),
        "median_rise_c": round(rise, 3),
        "p90_rise_c": round(f_x, 4),
        "reference_p90_rise_c": round(f_b, 4),
        "convergence_error_c": round(sum(contributions.values()) - (f_x - f_b), 5),
    }
