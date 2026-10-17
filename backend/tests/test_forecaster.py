import numpy as np
import torch

from backend.models.features import FUTURE_FEATURES, PAST_FEATURES
from backend.models.forecaster import HORIZON, ForecasterPredictor, HeatForecaster, future_windows


def _signals(n=90):
    return {"hr": 90 + np.linspace(0, 60, n), "wbgt": np.full(n, 30.0)}


def test_shapes_and_percentiles_never_cross():
    torch.manual_seed(0)
    m = HeatForecaster()
    past = torch.randn(3, 50, len(PAST_FEATURES))
    fut = torch.randn(3, 50, len(FUTURE_FEATURES))
    h = m.encode(past)
    mu, logv = m.now(h)
    assert h.shape == (3, 50, 64) and mu.shape == (3, 50)
    t = torch.tensor([[5, 20], [10, 49], [0, 30]])
    fc = m.forecast(h[torch.arange(3).unsqueeze(1).expand(3, 2), t].reshape(6, -1),
                    future_windows(fut, t).reshape(6, HORIZON, -1))
    assert fc.shape == (6, HORIZON, 3)
    assert (fc[..., 0] < fc[..., 1]).all() and (fc[..., 1] < fc[..., 2]).all()


def test_future_windows_pad_past_the_end_as_missing():
    fut = torch.zeros(1, 40, len(FUTURE_FEATURES))
    w = future_windows(fut, torch.tensor([[35]]))
    assert w.shape == (1, 1, HORIZON, len(FUTURE_FEATURES))
    flag = FUTURE_FEATURES.index("wbgt_missing")
    assert (w[0, 0, :4, flag] == 0).all()        # minutes 36..39 exist
    assert (w[0, 0, 4:, flag] == 1).all()        # past the end -> missing


def test_predictions_are_causal():
    # A heat spike at minute 60 must not change predictions made before minute 60.
    torch.manual_seed(0)
    pred = ForecasterPredictor([HeatForecaster().eval()])
    a = _signals()
    b = {k: v.copy() for k, v in a.items()}
    b["hr"][60:] = 185
    _, now_a, _, fc_a = pred.predict_signals(a)
    _, now_b, _, fc_b = pred.predict_signals(b)
    assert np.allclose(now_a[:60], now_b[:60])
    assert np.allclose(fc_a[:30], fc_b[:30])      # windows ending before minute 60 only see known-future inputs


def test_the_network_can_learn_something():
    # Tiny overfit check: loss must drop on a fixed small batch.
    import random
    from ml.data import Session
    from ml.train import compute_loss, make_batch, to_tensors
    rng = np.random.default_rng(0)
    items = []
    for i in range(4):
        n = 80
        ct = 37 + np.linspace(0, 1.2, n) + rng.normal(0, 0.02, n)
        hr = 90 + 50 * (ct - 37) + rng.normal(0, 2, n)
        items.append(to_tensors(Session(f"p{i}", f"s{i}", "sim", "x", {"hr": hr, "core_temp": ct})))
    torch.manual_seed(0)
    m = HeatForecaster()
    opt = torch.optim.Adam(m.parameters(), lr=3e-3)
    batch = make_batch(items, random.Random(0), k_times=8, hide_sensors=False)
    first = compute_loss(m, batch)[0].item()
    for _ in range(60):
        loss, _ = compute_loss(m, batch)
        opt.zero_grad()
        loss.backward()
        opt.step()
    assert loss.item() < first * 0.5
