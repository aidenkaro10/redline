import math

import numpy as np

from backend.models.kalman import ECTempKalman, expected_hr, run_kalman


def test_one_step_matches_hand_calculation():
    # Worked out by hand from the spec's equations, separately from the module.
    kf = ECTempKalman()
    s = kf.step(120.0)
    assert math.isclose(s.ct, 37.102261139221376, rel_tol=1e-12)
    assert math.isclose(s.v, 0.00048265924626889387, rel_tol=1e-12)


def test_resting_hr_at_start_temp_keeps_ct_steady():
    # h(37.1) is about 83 bpm: the heart rate the model expects at 37.1 °C.
    hr_rest = expected_hr(37.1)
    assert 82 < hr_rest < 84
    cts, _ = run_kalman([hr_rest] * 60)
    assert abs(cts[-1] - 37.1) < 1e-6


def test_hard_work_raises_ct_steadily():
    cts, _ = run_kalman([160.0] * 60)
    assert np.all(np.diff(cts) > 0)       # always climbing
    assert 38.5 < cts[-1] < 39.2          # about an hour at 160 bpm crosses 38.5


def test_missing_hr_holds_ct_and_grows_uncertainty():
    kf = ECTempKalman()
    for _ in range(10):
        kf.step(150.0)
    before = kf.state
    after = kf.step(None)
    assert after.ct == before.ct
    assert after.v > before.v


def test_run_kalman_accepts_nan_for_missing():
    cts, vs = run_kalman([150.0, float("nan"), 150.0])
    assert cts[1] == cts[0]
    assert np.all(vs > 0)


def test_variance_stays_positive_and_settles():
    _, vs = run_kalman([140.0] * 300)
    assert np.all(vs > 0)
    assert abs(vs[-1] - vs[-2]) / vs[-1] < 1e-5   # converged to a steady uncertainty
    assert 0.01 < vs[-1] < 0.02                    # about ±0.11 °C, a sane spread


def test_fused_with_one_source_is_exactly_ectemp():
    from backend import config
    a, b = ECTempKalman(), ECTempKalman()
    for hr in [100.0, 130.0, None, 150.0, 160.0]:
        sa = a.step(hr)
        sb = b.step_fused([(hr, config.KALMAN_SIGMA)])
        assert sa.ct == sb.ct and sa.v == sb.v


def test_fusion_trusts_the_cleaner_source_and_survives_dropouts():
    from backend import config
    watch_sigma, band_sigma = config.KALMAN_SIGMA, 3 * config.KALMAN_SIGMA
    clean, fused = ECTempKalman(), ECTempKalman()
    for _ in range(30):
        clean.step(150.0)
        fused.step_fused([(150.0, watch_sigma), (175.0, band_sigma)])   # band reads too high
    gap = fused.state.ct - clean.state.ct
    assert 0 < gap < 0.15                    # pulled only a little toward the noisy band
    before = fused.state.ct
    fused.step_fused([(None, watch_sigma), (150.0, band_sigma)])       # watch drops out
    assert fused.state.ct != before          # the band alone still updates the estimate
