"""Per-worker pipeline: minutes, Kalman, forecaster, alerts, band commands."""

from pathlib import Path

import numpy as np
import pytest

from backend import config
from backend.app.pipeline import WorkerPipeline
from backend.app.schemas import ButtonPress, Reading
from backend.models.kalman import run_kalman

CHECKPOINT = Path("ml/checkpoints/v1.pt")    # gitignored: symlink it from the main checkout


def feed(p, minutes, hr, start=0.0, every=5.0, **fields):
    """Send readings every 5 s for `minutes` minutes. Returns all events."""
    events = []
    n = int(minutes * 60 / every)
    for i in range(n):
        h = hr(start + i * every) if callable(hr) else hr
        events += p.handle(Reading(worker_id=p.worker_id, timestamp=start + i * every, hr=h, **fields))
    return events


def of(events, kind):
    return [e for e in events if e["type"] == kind]


def test_minute_averages_match_offline_kalman():
    p = WorkerPipeline("w1")
    hr = lambda t: 100 + (t // 60) * 2           # rises 2 bpm per minute
    events = feed(p, 10, hr)
    est = of(events, "estimate")
    assert len(est) == 9                          # minute 10 closes only when minute 11 starts
    offline, _ = run_kalman([100 + 2 * k for k in range(9)])
    assert np.allclose([e["kalman"] for e in est], offline, atol=1e-3)
    assert est[0]["model"] == "kalman" and est[0]["forecast"] is None


def test_hot_worker_yellow_then_red_commands():
    p = WorkerPipeline("w1")
    events = feed(p, 120, 175)
    cmds = [e["line"] for e in of(events, "band_command")]
    assert cmds[:2] == ["BUZZ:HEAT_YELLOW", "BUZZ:HEAT_RED"]
    assert all(e["worker_id"] == "w1" for e in events)


def test_gap_fills_missing_minutes_but_caps_long_silence():
    p = WorkerPipeline("w1")
    feed(p, 2, 90)                                       # minutes 0-1
    p.handle(Reading(worker_id="w1", timestamp=5 * 60 + 1, hr=90))    # 3 quiet minutes
    assert len(p.signals["hr"]) == 5
    assert np.isnan(p.signals["hr"][2]) and np.isnan(p.signals["hr"][4])
    p.tick(60 * 60)                                      # band off for ~55 minutes
    assert len(p.signals["hr"]) == 5 + config.MAX_GAP_MIN + 1


def test_demo_threshold_rechecks_immediately():
    p = WorkerPipeline("w1")
    feed(p, 30, 125)                       # warm, but under 38.0
    assert p.heat.level == "clear"
    assert 37.3 < p.estimates[-1]["core_temp"] < 38.0
    events = p.set_demo(True)
    assert of(events, "heat_alert") and of(events, "band_command")


def test_scripted_fall_mandown_then_confirmed():
    p = WorkerPipeline("w1", site={"site_id": "s1", "name": "Plant A", "lat": 29.6, "lon": -82.3},
                       info={"name": "Ana"})
    feed(p, 3, 110, tilt=0, activity=4.0)
    t = 180.0
    events = []
    while t < 180 + config.MANDOWN_STILL_S + config.MANDOWN_ESCALATE_S + 5:
        events += p.handle(Reading(worker_id="w1", timestamp=t, tilt=1, activity=0.0, hr=95))
        t += 1
    cmds = [e["line"] for e in of(events, "band_command")]
    assert cmds == ["BUZZ:MANDOWN"]
    conf = of(events, "mandown_confirmed")
    assert len(conf) == 1
    c = conf[0]
    assert c["worker_name"] == "Ana" and c["site_name"] == "Plant A" and c["location"]["lat"] == 29.6
    assert c["reason"] == "fall" and c["last_vitals"]["hr"] == 95
    assert c["seconds_since_start"] >= config.MANDOWN_STILL_S + config.MANDOWN_ESCALATE_S - 1


def test_button_cancels_mandown_check():
    p = WorkerPipeline("w1")
    for t in range(0, config.MANDOWN_STILL_S + 2):
        p.handle(Reading(worker_id="w1", timestamp=float(t), tilt=1, activity=0.0))
    assert p.mandown.state == "checking"
    events = p.handle(ButtonPress(worker_id="w1", timestamp=60.0))
    assert of(events, "mandown_cancelled")[0]["by"] == "button"
    assert of(events, "band_command")[0]["line"] == "BUZZ:OFF"


def test_unacknowledged_red_triggers_mandown_check():
    p = WorkerPipeline("w1", demo=True)
    events = feed(p, 60, 150)                   # no button, no activity field
    assert p.heat.level == "red"
    checks = of(events, "mandown_check")
    assert checks and checks[0]["reason"] == "heat_red_unacknowledged"


@pytest.mark.skipif(not CHECKPOINT.exists(), reason="forecaster checkpoint not in this checkout")
def test_forecaster_estimates():
    from backend.models.forecaster import ForecasterPredictor
    p = WorkerPipeline("w1", predictor=ForecasterPredictor.load(CHECKPOINT))
    events = feed(p, 15, 140)
    est = of(events, "estimate")[-1]
    assert est["model"].startswith("v1")
    fc = est["forecast"]
    assert len(fc["p90"]) == 30
    assert all(lo <= mid <= hi for lo, mid, hi in zip(fc["p10"], fc["p50"], fc["p90"]))


def test_tick_before_first_reading_does_not_start_shift():
    p = WorkerPipeline("w1")
    p.tick(10_000.0)
    feed(p, 3, 100, start=9_000.0)          # pre-roll readings stamped in the past
    assert len(p.signals["hr"]) == 2


@pytest.mark.skipif(not CHECKPOINT.exists(), reason="forecaster checkpoint not in this checkout")
def test_known_future_context_reaches_forecaster():
    from backend.models.forecaster import ForecasterPredictor
    predictor = ForecasterPredictor.load(CHECKPOINT)
    a, b = WorkerPipeline("a", predictor=predictor), WorkerPipeline("b", predictor=predictor)
    b.set_context(workload=100.0, future_workload=[100.0] * 30, future_wbgt=[25.0] * 30, wbgt=25.0)
    ea = of(feed(a, 20, 140), "estimate")[-1]
    eb = of(feed(b, 20, 140), "estimate")[-1]
    assert ea["kalman"] == eb["kalman"]                          # same heart rate, same Kalman
    assert ea["forecast"]["p90"][19] != eb["forecast"]["p90"][19]


def test_mandown_confirmed_matches_session_d_fields():
    p = WorkerPipeline("w1", site={"site_id": "s1", "name": "Plant A", "lat": 29.6, "lon": -82.3})
    p.trigger_mandown(0.0, "heat_red_unacknowledged")
    ev = of(p.tick(config.MANDOWN_ESCALATE_S), "mandown_confirmed")[0]
    for key in ("worker_id", "worker_name", "site_id", "site_name", "lat", "lon", "trigger",
                "started_at", "confirmed_at", "last_movement_at", "core_temp_c", "hr_bpm", "skin_temp_c"):
        assert key in ev
    assert ev["trigger"] == "heat_red_unacked" and ev["confirmed_at"] == config.MANDOWN_ESCALATE_S


def test_sos_line_emits_sos_event_without_cancelling():
    from backend.app.schemas import events_from_line
    p = WorkerPipeline("w1")
    p.trigger_mandown(0.0, "demo")
    events = []
    for e in events_from_line("BTN:SOS", "w1", 5.0):
        events += p.handle(e)
    sos = of(events, "sos")
    assert sos and sos[0]["pressed_at"] == 5.0 and "worker_name" in sos[0]
    assert p.mandown.state == "checking"


def test_two_heart_rate_sources_are_fused():
    from backend.models.kalman import ECTempKalman
    p = WorkerPipeline("w1")
    for i in range(24):                      # 2 minutes: watch says 150, band pulse sensor says 120
        t = i * 5.0
        p.handle(Reading(worker_id="w1", timestamp=t, hr=150, source="watch"))
        p.handle(Reading(worker_id="w1", timestamp=t + 1, hr=120, source="band"))
    est = p.estimates[0]
    assert est["hr_watch"] == 150 and est["hr_band"] == 120 and est["hr_sources"] == ["watch", "band"]
    # Kalman = step_fused with each device's sigma
    kf = ECTempKalman()
    expected = kf.step_fused([(150.0, config.KALMAN_SIGMA_WATCH), (120.0, config.KALMAN_SIGMA_BAND_PPG)])
    assert abs(est["kalman"] - expected.ct) < 1e-3
    # Blended HR for the forecaster leans to the watch (4x the weight at 2x the sigma)
    assert est["hr"] == pytest.approx((150 * 4 + 120) / 5, abs=0.01)


def test_band_only_hr_uses_band_sigma_and_watch_only_matches_ectemp():
    from backend.models.kalman import ECTempKalman
    band, watch = WorkerPipeline("b"), WorkerPipeline("w")
    for i in range(24):
        band.handle(Reading(worker_id="b", timestamp=i * 5.0, hr=150, source="band"))
        watch.handle(Reading(worker_id="w", timestamp=i * 5.0, hr=150, source="watch"))
    kb, kw = ECTempKalman(), ECTempKalman()
    assert band.estimates[0]["kalman"] == pytest.approx(kb.step_fused([(150.0, config.KALMAN_SIGMA_BAND_PPG)]).ct, abs=1e-3)
    assert watch.estimates[0]["kalman"] == pytest.approx(kw.step(150.0).ct, abs=1e-3)
    assert band.estimates[0]["hr_sources"] == ["band"]


def test_personal_offset_from_spot_checks():
    from backend.models.personal_offset import PRIOR_CHECKS
    p = WorkerPipeline("w1")
    feed(p, 10, 140)
    raw = p.estimates[-1]["core_temp_raw"]
    ev = p.spot_check(raw - 0.4, p.estimates[-1]["timestamp"])[0]     # model reads 0.4 °C hot
    assert ev["type"] == "offset_update" and ev["accepted"] and ev["n_checks"] == 1
    assert ev["offset_c"] == pytest.approx(0.4 / (1 + PRIOR_CHECKS), abs=1e-3)
    feed(p, 2, 140, start=600.0)
    e = p.estimates[-1]
    assert e["core_temp"] == pytest.approx(e["core_temp_raw"] - ev["offset_c"], abs=1e-3)
    assert p.spot_check(raw + 5.0, e["timestamp"])[0]["accepted"] is False     # bad reading ignored


def test_offset_applies_to_every_forecast_percentile():
    from pathlib import Path
    if not CHECKPOINT.exists():
        pytest.skip("no checkpoint")
    from backend.app.model_runner import load_best_predictor
    pred = load_best_predictor(explicit=str(CHECKPOINT))
    a, b = WorkerPipeline("a", predictor=pred), WorkerPipeline("b", predictor=pred)
    b.offset.misses = [0.3, 0.3, 0.3]
    ea, eb = of(feed(a, 12, 140), "estimate")[-1], of(feed(b, 12, 140), "estimate")[-1]
    o = b.offset.offset_c
    for q in ("p10", "p50", "p90"):
        assert np.allclose(np.array(ea["forecast"][q]) - o, eb["forecast"][q], atol=2e-3)
