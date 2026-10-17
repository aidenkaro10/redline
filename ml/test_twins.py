import numpy as np
import torch

from backend.models.forecaster import HeatForecaster
from ml.data import Session
from ml.twins import TWIN_MAX_NORM, adapt_twin


def _session(pid="p1", n=120, seed=0):
    rng = np.random.default_rng(seed)
    hr = 100 + 30 * np.sin(np.arange(n) / 15) + rng.normal(0, 2, n)
    ct = 37 + np.linspace(0, 1, n)
    return Session(pid, f"{pid}-s", "sim", "x", {"hr": hr, "core_temp": ct})


def test_adaptation_never_touches_the_network_and_respects_the_cap():
    torch.manual_seed(0)
    m = HeatForecaster(twin_dim=16, hr_head=True)
    before = {k: v.clone() for k, v in m.state_dict().items()}
    r = adapt_twin(m, [_session()], steps=20)
    for k, v in m.state_dict().items():
        assert torch.equal(v, before[k])                       # network frozen
    assert np.linalg.norm(r.twin) <= TWIN_MAX_NORM + 1e-5      # capped
    assert all(p.requires_grad for p in m.parameters())        # left trainable afterwards


def test_gate_rolls_back_when_there_is_no_gain():
    torch.manual_seed(0)
    m = HeatForecaster(twin_dim=16, hr_head=True)
    r = adapt_twin(m, [_session()], steps=0)                    # no steps: nothing can improve
    assert not r.kept
    assert np.allclose(r.twin, 0)                               # back to the average person


def test_adaptation_uses_heart_rate_only():
    # Changing the core temp must not change the learned twin at all.
    torch.manual_seed(0)
    m = HeatForecaster(twin_dim=16, hr_head=True)
    a = _session()
    b = _session()
    b.signals["core_temp"] = b.signals["core_temp"] + 1.5
    ra = adapt_twin(m, [a], steps=15)
    rb = adapt_twin(m, [b], steps=15)
    assert np.allclose(ra.twin, rb.twin)
