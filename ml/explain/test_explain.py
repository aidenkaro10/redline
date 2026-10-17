"""Small tests for ml/explain/explain.py. Run:  pytest ml/explain"""

from pathlib import Path

import numpy as np
import pytest
import torch

from backend.models.forecaster import ForecasterPredictor, HeatForecaster
from ml.explain import explain as E

CKPT = Path("/Users/Annabelle/Documents/projects/planit-control/ml/checkpoints/ens5.pt")


@pytest.fixture(scope="module")
def tiny():
    """Two small random networks: no checkpoint needed, enough to test the maths."""
    torch.manual_seed(0)
    return ForecasterPredictor([HeatForecaster(hidden=16, layers=2) for _ in range(2)])


@pytest.fixture(scope="module")
def ens5():
    if not CKPT.exists():
        pytest.skip("ens5.pt not found")
    return ForecasterPredictor.load(CKPT)


def _shift(n=80, hr0=70.0, hr1=150.0, wbgt=30.0, workload=300.0):
    ramp = np.linspace(hr0, hr1, n)
    return {"hr": ramp, "wbgt": np.full(n, wbgt), "workload": np.full(n, workload),
            "skin_temp": np.full(n, 35.0), "activity": np.full(n, 6.0)}


def test_attributions_add_up_to_the_forecast_difference(tiny):
    out = E.explain(_shift(), 50, tiny)
    total = sum(out["contributions"].values())
    assert total == pytest.approx(out["p90_rise_c"] - out["reference_p90_rise_c"], abs=0.01)
    assert abs(out["convergence_error_c"]) < 0.01


def test_attributions_add_up_on_the_real_ensemble(ens5):
    out = E.explain(_shift(), 50, ens5)
    assert abs(out["convergence_error_c"]) < 0.01
    assert set(out["contributions"]) == set(E.GROUP_NAMES)


def test_reference_like_session_has_no_attributions(tiny):
    """A session identical to the calm reference gets zero for every group."""
    n = 60
    calm = {"hr": np.full(n, 65.0), "skin_temp": np.full(n, 33.5), "activity": np.zeros(n),
            "wbgt": np.full(n, 20.0), "workload": np.full(n, 60.0)}
    out = E.explain(calm, 40, tiny)
    # only the 'time' group can differ (the reference worker has just started)
    others = {g: c for g, c in out["contributions"].items() if g != "time"}
    assert max(abs(c) for c in others.values()) < 1e-4


def test_steady_session_has_smaller_push_than_a_ramp(ens5):
    steady = _shift(hr0=100, hr1=100, wbgt=22, workload=100)
    ramp = _shift(hr0=70, hr1=170, wbgt=33, workload=450)
    a = E.explain(steady, 60, ens5)
    b = E.explain(ramp, 60, ens5)
    assert abs(sum(a["contributions"].values())) < abs(sum(b["contributions"].values()))


def test_only_the_past_is_used(tiny):
    """Changing heart rate AFTER the explained minute must not change anything."""
    sig = _shift()
    a = E.explain(sig, 40, tiny)
    sig2 = {k: v.copy() for k, v in sig.items()}
    sig2["hr"][41:] = 190.0
    b = E.explain(sig2, 40, tiny)
    assert a["contributions"] == b["contributions"]


def test_missing_sensors_get_zero_credit(tiny):
    sig = {"hr": np.linspace(70, 150, 80)}          # no weather, no skin temp, no activity
    out = E.explain(sig, 50, tiny)
    for g in ("wbgt", "workload", "skin_temp", "activity"):
        assert out["contributions"][g] == 0.0


def test_why_line_wording():
    facts = {"hr": 150.0, "hr_jump": 18.0, "core": 38.0, "core_climb": 0.3, "wbgt": 31.0,
             "workload": 350.0, "skin": 36.0, "activity": 8.0, "minutes": 95}
    c = dict.fromkeys(E.GROUP_NAMES, 0.0) | {"heart_rate": 0.3, "wbgt": 0.2, "workload": 0.01}
    why = E.make_why("rising", c, facts)
    assert why == "Rising because heart rate jumped 18 bpm in 10 min and it's 31 °C WBGT."
    assert "—" not in why
    c2 = dict.fromkeys(E.GROUP_NAMES, 0.0) | {"heart_rate": -0.2}
    facts2 = facts | {"hr": 85.0, "hr_jump": -12.0}
    assert E.make_why("falling", c2, facts2) == "Falling because heart rate dropped 12 bpm in 10 min."
    assert E.make_why("steady", dict.fromkeys(E.GROUP_NAMES, 0.0), facts).startswith("Holding steady")
