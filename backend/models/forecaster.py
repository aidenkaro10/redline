"""
The neural network: estimates core temp now and forecasts it 1 to 30 minutes ahead.

    every minute so far ──> [encoder GRU] ──> memory at minute t ──┬─> NOW head: core temp now (mean + uncertainty)
                                                                   └─> [decoder GRU] over the next 30 minutes,
                                                                       fed planned work + forecast weather
                                                                       ──> 10th / 50th / 90th percentile per minute

Key choices:
- Everything is predicted as a CHANGE from the current Kalman estimate. The net
  starts from the Army's physics model and learns what it misses.
- The encoder is causal: the memory at minute t only saw minutes 0..t.
- The 10th and 90th percentiles are built as "median minus/plus a positive gap",
  so they can never cross the median.
- Tens of thousands of parameters. Trains on a laptop CPU.

Training lives in ml/train.py. This file is what the live server loads.
"""

from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from backend import config
from backend.models.features import FUTURE_FEATURES, PAST_FEATURES, build_features, kalman_baseline

HORIZON = max(config.FORECAST_HORIZONS_MIN)     # 30 minutes
QUANTILES = config.FORECAST_QUANTILES           # (0.1, 0.5, 0.9)


class HeatForecaster(nn.Module):
    """
    twin_dim > 0 turns on personal twins: a small vector (the worker's "fingerprint")
    fed into every minute of the encoder and decoder. All zeros = the average person,
    so a brand-new worker works on day one. See ml/twins.py for how a twin is learned.

    hr_head = True adds a second output: the next 10 minutes of heart rate (as a change
    from now, in units of 30 bpm). Heart rate is observable live, so the twin can learn
    from it without ever measuring core temp.
    """

    HR_STEPS = 10

    def __init__(self, hidden: int = 64, layers: int = 2, dropout: float = 0.1,
                 twin_dim: int = 0, hr_head: bool = False):
        super().__init__()
        self.hidden, self.layers, self.twin_dim, self.hr_head = hidden, layers, twin_dim, hr_head
        n_past, n_future = len(PAST_FEATURES), len(FUTURE_FEATURES)
        self.encoder = nn.GRU(n_past + twin_dim, hidden, layers, batch_first=True, dropout=dropout)
        self.now_head = nn.Sequential(nn.Linear(hidden, hidden), nn.GELU(), nn.Linear(hidden, 2))
        self.to_decoder = nn.Linear(hidden, hidden * layers)
        # decoder input: known future features + "how many minutes ahead" (k / 30) + twin
        self.decoder = nn.GRU(n_future + 1 + twin_dim, hidden, layers, batch_first=True, dropout=dropout)
        self.out = nn.Linear(hidden, 3 + (1 if hr_head else 0))

    def _twin(self, twin: torch.Tensor | None, n: int, like: torch.Tensor) -> torch.Tensor:
        """The twin for n rows, or the average person (zeros) if none given."""
        if twin is None:
            return torch.zeros(n, self.twin_dim, dtype=like.dtype, device=like.device)
        twin = twin.reshape(-1, self.twin_dim)               # (twin_dim,) -> (1, twin_dim)
        return twin.expand(n, -1) if twin.shape[0] == 1 else twin

    def encode(self, past: torch.Tensor, twin: torch.Tensor | None = None) -> torch.Tensor:
        """past: (B, T, n_past), twin: (B, twin_dim) or (twin_dim,) -> memory (B, T, hidden)."""
        if self.twin_dim:
            B, T, _ = past.shape
            tw = self._twin(twin, B, past)
            past = torch.cat([past, tw.unsqueeze(1).expand(B, T, self.twin_dim)], dim=-1)
        h, _ = self.encoder(past)
        return h

    def now(self, h: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """Change from Kalman (°C) and log-variance, at every minute."""
        out = self.now_head(h)
        return out[..., 0], out[..., 1].clamp(-10, 3)

    def forecast_full(self, h_t: torch.Tensor, future: torch.Tensor, twin: torch.Tensor | None = None):
        """
        h_t:    (N, hidden) memory at the forecast moment
        future: (N, 30, n_future) known inputs for minutes t+1..t+30
        twin:   (N, twin_dim) or None
        returns quantiles (N, 30, 3) change from Kalman at percentiles 10 / 50 / 90,
                and heart rate change (N, 10) in units of 30 bpm (None without hr_head)
        """
        n = h_t.shape[0]
        h0 = self.to_decoder(h_t).view(n, self.layers, self.hidden).transpose(0, 1).contiguous()
        step = torch.arange(1, HORIZON + 1, device=h_t.device, dtype=h_t.dtype).div(HORIZON)
        parts = [future, step.view(1, HORIZON, 1).expand(n, -1, -1)]
        if self.twin_dim:
            parts.append(self._twin(twin, n, h_t).unsqueeze(1).expand(n, HORIZON, self.twin_dim))
        y, _ = self.decoder(torch.cat(parts, dim=-1), h0)
        raw = self.out(y)
        median = raw[..., 1]
        low = median - F.softplus(raw[..., 0]) - 1e-3
        high = median + F.softplus(raw[..., 2]) + 1e-3
        hr = raw[:, :self.HR_STEPS, 3] if self.hr_head else None
        return torch.stack([low, median, high], dim=-1), hr

    def forecast(self, h_t: torch.Tensor, future: torch.Tensor, twin: torch.Tensor | None = None) -> torch.Tensor:
        return self.forecast_full(h_t, future, twin)[0]


def future_windows(future: torch.Tensor, t: torch.Tensor) -> torch.Tensor:
    """
    For each (batch row b, minute t), gather the known-future rows t+1..t+30.
    Past the end of the session: zeros with every "missing" flag set to 1.
    future: (B, T, n_future), t: (B, K) -> (B, K, 30, n_future)
    """
    B, T, Ff = future.shape
    pad = torch.zeros(B, HORIZON, Ff, dtype=future.dtype, device=future.device)
    for name in FUTURE_FEATURES:
        if name.endswith("_missing"):
            pad[..., FUTURE_FEATURES.index(name)] = 1.0
    padded = torch.cat([future, pad], dim=1)                       # (B, T + 30, Ff)
    idx = t.unsqueeze(-1) + torch.arange(1, HORIZON + 1, device=future.device)   # (B, K, 30)
    b = torch.arange(B, device=future.device).view(B, 1, 1).expand_as(idx)
    return padded[b, idx]


# ---------------------------------------------------------------------------
# Inference wrapper: what the scoring harness and the live server use
# ---------------------------------------------------------------------------
class ForecasterPredictor:
    """Wraps one or more trained networks (an ensemble) behind the harness interface."""

    def __init__(self, models: list[HeatForecaster], trained_on: set[str] | None = None, name: str = "forecaster",
                 calibration: dict | None = None):
        self.models = [m.eval() for m in models]
        self.trained_on = trained_on or set()
        self.name = name
        # Conformal calibration from ml/calibrate.py:
        #   "offsets":   (30, 3) shift added to the 10th / 50th / 90th percentile, per minute ahead
        #   "now_scale": multiplier for the NOW uncertainty
        # None = raw, uncalibrated model.
        self.calibration = calibration

    @classmethod
    def load(cls, path: str | Path, calibrated: bool = True) -> "ForecasterPredictor":
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        models = []
        for state in ckpt["models"]:
            m = HeatForecaster(**ckpt["model_args"])
            m.load_state_dict(state)
            models.append(m)
        cal = ckpt.get("calibration") if calibrated else None
        name = ckpt.get("name", Path(path).stem) + ("+conformal" if cal else "")
        # People used for calibration count as "seen" (the harness won't score on them),
        # but only when that calibration is actually applied.
        seen = set(ckpt.get("trained_on", [])) | (set(ckpt.get("calibrated_on", [])) if cal else set())
        return cls(models, seen, name, cal)

    def fit(self, sessions):
        pass   # trained separately in ml/train.py

    @torch.no_grad()
    def predict_signals(self, signals: dict[str, np.ndarray], twins: list | None = None):
        """
        twins: one twin vector per ensemble model (from ml/twins.py), or None for the
        average person. Ignored by models trained without twins.
        """
        past, future = build_features(signals)
        kal = kalman_baseline(signals)
        n = len(kal)
        p = torch.from_numpy(past).unsqueeze(0)
        f = torch.from_numpy(future).unsqueeze(0)
        t = torch.arange(n).unsqueeze(0)
        nows, vars_, fcs = [], [], []
        for i, m in enumerate(self.models):
            tw = None
            if m.twin_dim and twins is not None and twins[i] is not None:
                tw = torch.as_tensor(np.asarray(twins[i]), dtype=torch.float32)
            h = m.encode(p, tw)
            d, logv = m.now(h)
            fc = m.forecast(h[0], future_windows(f, t)[0], tw)      # (n, 30, 3)
            nows.append(d[0].numpy())
            vars_.append(np.exp(logv[0].numpy()))
            fcs.append(fc.numpy())
        now_delta = np.mean(nows, axis=0)
        # ensemble uncertainty = average model variance + disagreement between models
        now_var = np.mean(vars_, axis=0) + np.var(nows, axis=0)
        fc = np.stack(fcs)                                           # (models, n, 30, 3)
        median = fc[..., 1].mean(axis=0)
        spread = fc[..., 1].std(axis=0)
        low = fc[..., 0].mean(axis=0) - spread
        high = fc[..., 2].mean(axis=0) + spread
        if self.calibration is not None:
            off = np.asarray(self.calibration["offsets"])           # (30, 3)
            low, median, high = low + off[:, 0], median + off[:, 1], high + off[:, 2]
            low, high = np.minimum(low, median), np.maximum(high, median)   # keep the order
            now_var = now_var * self.calibration["now_scale"] ** 2
        return kal, now_delta, now_var, np.stack([low, median, high], axis=-1)

    def predict(self, session):
        from ml.evaluate import Prediction     # imported here so the server doesn't need ml/
        kal, now_delta, now_var, fc = self.predict_signals(session.signals, session.meta.get("twins"))
        forecast = {}
        for h in config.FORECAST_HORIZONS_MIN:
            forecast[h] = {q: kal + fc[:, h - 1, i] for i, q in enumerate(QUANTILES)}
        return Prediction(now=kal + now_delta, forecast=forecast, now_std=np.sqrt(now_var))
