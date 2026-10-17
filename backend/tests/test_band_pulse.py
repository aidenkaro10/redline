"""Heart rate from the band's raw PPG (backend/ingest/band_pulse.py): right number, or no number."""

import numpy as np

from backend import config
from backend.ingest import band_synth
from backend.ingest.band_pulse import PulseEstimator, estimate_window, naive_peak_hr

FS = config.BAND_SAMPLE_HZ
WIN = int(config.BAND_PPG_WINDOW_S * FS)


def test_rest_gives_the_right_heart_rate():
    for seed in range(5):
        rec = band_synth.session("rest", 30, seed)
        r = estimate_window(rec["ppg"][-WIN:], rec["acc"][-WIN:])
        truth = rec["hr"][-config.BAND_PPG_WINDOW_S:].mean()
        assert r.hr is not None and abs(r.hr - truth) < 3, (seed, r, truth)
        assert 0 <= r.quality <= 1 and r.reason == "ok"


def test_no_number_when_the_sensor_sees_nothing_useful():
    assert estimate_window(np.full(WIN, 2000)).hr is None                      # flat: off the wrist
    assert estimate_window(np.full(WIN, 2000)).reason == "flat"
    clipped = np.full(WIN, 4095)
    clipped[::7] = 4000
    assert estimate_window(clipped).reason == "clipped"                         # too much light
    noise = np.random.default_rng(0).normal(2000, 30, WIN)
    assert estimate_window(noise).hr is None                                    # random noise: no rhythm


def test_arm_rhythm_without_a_pulse_is_not_reported_as_heart_rate():
    """Walking with the sensor seeing only the arm swing (no pulse at all): never a number."""
    rng = np.random.default_rng(1)
    for _ in range(5):
        acc, f = band_synth.motion("walk", WIN, rng)
        dyn = acc - acc.mean(axis=0)
        ppg = 2000 + dyn @ rng.normal(0, 150, 3) + rng.normal(0, 1, WIN)
        r = estimate_window(ppg, acc)
        assert r.hr is None, r
        # ...whereas a naive beat counter happily reports the arm swing as a heart rate
        assert naive_peak_hr(ppg) is not None


def test_streaming_gives_one_answer_every_5_s_and_resets_after_a_gap():
    rec = band_synth.session("rest", 40, 3)
    est = PulseEstimator()
    results = []
    for i in range(0, len(rec["ppg"]), 10):           # one PPG line = 10 samples, every 200 ms
        t = (i + 10) / FS
        for k in range(i, i + 10):
            est.add_acc(t, tuple(rec["acc"][k]))
        r = est.add_ppg(t, rec["ppg"][i:i + 10].tolist())
        if r is not None:
            results.append(r)
    times = [r.t for r in results]
    assert times[0] == config.BAND_PPG_WINDOW_S                  # first answer once the window is full
    assert np.allclose(np.diff(times), config.BAND_PPG_STEP_S)   # then every 5 s
    assert all(r.hr is not None for r in results)
    # A 2 s gap in the lines: start over, no answer until a new full window.
    assert est.add_ppg(times[-1] + 2.0, rec["ppg"][:10].tolist()) is None
    assert len(est.ppg) == 10


def test_accuracy_beats_the_naive_counter_while_walking():
    """Small version of tools/eval_band_pulse.py: given numbers are close, naive ones aren't."""
    ours, naive = [], []
    for seed in range(4):
        rec = band_synth.session("walk", 120, 500 + seed)
        state, prev = {}, None
        for end in range(WIN, len(rec["ppg"]) + 1, int(config.BAND_PPG_STEP_S * FS)):
            truth = rec["hr"][(end - WIN) // FS:end // FS].mean()
            r = estimate_window(rec["ppg"][end - WIN:end], rec["acc"][end - WIN:end], prev_hr=prev, nlms_state=state)
            prev = r.hr if r.hr is not None else prev
            if r.hr is not None:
                ours.append(abs(r.hr - truth))
            naive.append(abs(naive_peak_hr(rec["ppg"][end - WIN:end]) - truth))
    assert ours, "expected at least some confident walking windows"
    assert np.mean(ours) < 5 and np.mean(naive) > 15
