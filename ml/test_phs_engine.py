"""
Guards for the heat strain engine the simulator is built on (pythermalcomfort PHS).
If a library update breaks either behavior, these fail loudly.
"""

import warnings

import numpy as np
import pytest
from pythermalcomfort.models import phs

STATE = ("t_re", "t_sk", "t_cr", "t_cr_eq", "t_sk_t_cr_wg", "sweat_rate_watt", "evap_load_wm2_min")
BASE = dict(v=0.3, clo=0.5, posture="standing", round_output=False, limit_inputs=False,
            model="7933-2004", weight=80, height=1.78)


def _step(state, **env):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = phs(**BASE, **env, duration=1, **state)
    return r, {k: np.asarray(getattr(r, k)) for k in STATE}


def test_chained_one_minute_steps_equal_one_long_run():
    env = dict(tdb=35, tr=45, rh=50, met=3.5)
    state = {}
    for _ in range(30):
        r, state = _step(state, **env)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ref = phs(**BASE, **env, duration=30)
    assert float(r.t_re) == pytest.approx(float(ref.t_re), abs=1e-9)


def test_batched_workers_equal_workers_run_alone():
    tdb = np.array([30.0, 36.0])
    met = np.array([2.0, 4.0])
    batch, alone = {}, [{}, {}]
    for _ in range(20):
        rb, batch = _step(batch, tdb=tdb, tr=tdb + 10, rh=60, met=met)
        for i in range(2):
            ri, alone[i] = _step(alone[i], tdb=float(tdb[i]), tr=float(tdb[i]) + 10, rh=60, met=float(met[i]))
            assert float(rb.t_re[i]) == pytest.approx(float(ri.t_re), abs=1e-9)
