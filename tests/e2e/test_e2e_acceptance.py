"""
Backend acceptance items (docs/testing/backend-acceptance.md) not already covered by the other
e2e files. Each test is named after its item number (test_A01_..., test_A12_...).
The full item -> test map is in docs/sessions/e2e-tests.md.
"""

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from backend import config
from backend.alerts.heat import HeatAlerter, HeatInputs
from backend.alerts.router import BandRouter
from backend.app.schemas import events_from_line
from backend.emergency.escalation import EscalationLog, EscalationManager
from backend.emergency.events import MandownConfirmed
from backend.emergency.guard import RefusedNumber, check_number
from backend.emergency.phones import FakePhone
from backend.emergency.report import build_report, emergency_script
from backend.models.kalman import ECTempKalman
from backend.replay import shifts
from e2e_support import TEST_NUMBERS, StepClock

NEAR_MISS = ["TEMP:", "TEMP:abc", "ACC:1,2", "BTN:2", "BTN:", "TILT:x,ACT:1", "HR:", ":", "TEMP",
             "TEMP 34.5", "\x00TEMP\x00", "A" * 100_000, "TEMP:34.5" + "\x00" * 50, "#TEMP:34.5", ",TEMP:34"]
BAD_VALUES = ["TEMP:nan", "HR:inf", "HR:-80", "TEMP:1e9", "HR:" + "9" * 400]


# ---------------------------------------------------------------------------
# 1. Band protocol and inputs
# ---------------------------------------------------------------------------
def test_A01_fuzzed_lines_never_crash_and_near_misses_never_make_readings():
    rng = random.Random(1)
    for _ in range(10_000):
        raw = bytes(rng.randrange(256) for _ in range(rng.randrange(60))).decode("utf-8", errors="replace")
        events_from_line(raw, "w", 1.0)                         # must never raise
    for line in NEAR_MISS:
        assert events_from_line(line, "w", 1.0) == [], line
    for line in ("TEMP:34.62", "TILT:1,ACT:7", "ACC:0.98,0.02,0.11", "HR:120", "BTN:1", "BTN:SOS"):
        assert len(events_from_line(line, "w", 1.0)) == 1, line


@pytest.mark.xfail(strict=True, reason="BUG -> session A: the parser accepts impossible values (TEMP:nan, HR:inf, "
                   "HR:-80, TEMP:1e9) as readings; one glitched line can poison a minute average / the Kalman")
def test_A01_impossible_values_never_make_readings():
    assert [line for line in BAD_VALUES if events_from_line(line, "w", 1.0)] == []


def test_A06_button_press_acknowledges_and_never_escalates(live_app):
    app = live_app()
    for ev in events_from_line("BTN:1", "w1", 1000.0) + events_from_line("HR:120", "w1", 1001.0):
        app.on_loop(app.hub.handle_input, ev, "serial")
    app.step(5)
    assert app.of_type("sos") == [] and app.of_type("emergency_status") == []
    assert app.phone.asked == []


# ---------------------------------------------------------------------------
# 2. Models
# ---------------------------------------------------------------------------
def test_A07_step_fused_with_one_source_equals_step():
    a, b = ECTempKalman(), ECTempKalman()
    rng = random.Random(7)
    for _ in range(240):
        hr = None if rng.random() < 0.05 else rng.uniform(70, 180)
        s1 = a.step(hr)
        s2 = b.step_fused([(hr, config.KALMAN_SIGMA_WATCH)])
        assert s2.ct == pytest.approx(s1.ct, abs=1e-12) and s2.v == pytest.approx(s1.v, abs=1e-12)


def test_A08_corrupt_checkpoint_falls_back_to_kalman_only(live_app, tmp_path):
    bad = tmp_path / "broken.pt"
    bad.write_bytes(b"this is not a torch checkpoint")
    app = live_app("--checkpoint", str(bad))
    assert app.hub.predictor is None
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(10 * 60)
    ests = app.of_type("estimate", "demo-1")
    assert len(ests) >= 9 and all(e["model"] == "kalman" and e["forecast"] is None for e in ests)


# ---------------------------------------------------------------------------
# 3. Alerts
# ---------------------------------------------------------------------------
def levels(alerter, values, t0=0.0):
    out = []
    for i, v in enumerate(values):
        alerter.update(t0 + 60 * i, HeatInputs(v))
        out.append(alerter.level)
    return out


def test_A12_red_fires_exactly_at_the_threshold_and_clears_only_below_the_margin():
    h = HeatAlerter()
    assert levels(h, [37.99]) == ["clear"]
    assert levels(h, [38.0]) == ["yellow"]                    # Kalman rule: estimate >= 38.0
    assert levels(h, [38.49]) == ["yellow"]
    assert levels(h, [38.50]) == ["red"]
    # Hovering 38.45-38.55 never flickers; it clears only below 38.5 - 0.1.
    assert levels(h, [38.45, 38.55, 38.41, 38.52, 38.40]) == ["red"] * 5
    assert levels(h, [38.39]) != ["red"]


def test_A13_demo_threshold_changes_only_via_the_control_and_is_flagged(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(30 * 60)
    assert all(not e["demo"] for e in app.of_type("heat_alert"))
    app.client.post("/api/demo/threshold", json={"on": True})
    app.step(5 * 60)
    on = app.of_type("heat_alert")
    assert on and on[-1]["demo"] is True and on[-1]["threshold"] in (config.DEMO_HEAT_WARNING_C, config.DEMO_HEAT_DANGER_C)
    snap = app.client.get("/api/state?view=demo").json()
    assert snap["demo"]["demo_threshold"] is True
    assert snap["history"]["demo-1"]["estimates"][-1]["thresholds"]["demo"] is True


def test_A15_router_priority_and_fallback_to_the_next_active_pattern():
    r = BandRouter("w")
    sent = r.set_many({p: True for p in ("FLOOD_FIRE", "HEAT_YELLOW", "LIGHTNING", "HEAT_RED", "MANDOWN")}, 0.0)
    assert [e["line"] for e in sent] == ["BUZZ:MANDOWN"]
    order = []
    for top in ("MANDOWN", "HEAT_RED", "LIGHTNING", "HEAT_YELLOW", "FLOOD_FIRE"):
        order += [e["line"] for e in r.set(top, False, 1.0)]
    assert order == ["BUZZ:HEAT_RED", "BUZZ:LIGHTNING", "BUZZ:HEAT_YELLOW", "BUZZ:FLOOD_FIRE", "BUZZ:OFF"]
    assert r.set("FLOOD_FIRE", False, 2.0) == []                # no change, no command


def test_A16_no_command_storm_over_a_whole_replay(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=2))
    app.step(70 * 60)
    for wid in app.replay.workers:
        lines = app.band.lines_for(wid)
        assert all(a != b for a, b in zip(lines, lines[1:])), f"{wid} got a repeated command: {lines}"
        assert len(lines) <= 6


def test_A17_flapping_tilt_switch_does_not_spam_checks(live_app):
    app = live_app()
    t = 1000.0
    for i in range(300):                                        # 5 minutes: tipped, upright, tipped ...
        for ev in events_from_line(f"TILT:{i % 2},ACT:{0 if i % 2 else 1}", "w1", t + i):
            app.on_loop(app.hub.handle_input, ev, "serial")
    app.on_loop(app.hub.pipelines["w1"].tick, t + 300)
    assert len(app.of_type("mandown_check", "w1")) <= 1


# ---------------------------------------------------------------------------
# 4. Emergency calls
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("number", ["911", "+1 911", "9-1-1", "(911)", "1911", "9911", "112", "999", "988",
                                    "933", "000", "1-1-2", "  911  ", "#911", "*911", "12345"])
def test_A18_blocked_numbers_are_refused_even_if_allowlisted(number):
    with pytest.raises(RefusedNumber):
        check_number(number, allowlist={number})


def test_A18_only_allowlisted_numbers_can_be_called():
    assert check_number(TEST_NUMBERS["crew_lead"], {TEST_NUMBERS["crew_lead"]}) == TEST_NUMBERS["crew_lead"]
    with pytest.raises(RefusedNumber):
        check_number("+13525550199", {TEST_NUMBERS["crew_lead"]})


def test_A19_live_dialer_is_never_built_in_dry_run(live_app, monkeypatch):
    from backend.emergency import voice_agent
    built = []
    real = voice_agent.ElevenLabsPhone.__init__
    monkeypatch.setattr(voice_agent.ElevenLabsPhone, "__init__", lambda self, *a, **k: (built.append(1), real(self, *a, **k)))
    assert config.EMERGENCY_LIVE_CALLS is False
    app = live_app()
    assert app.app.state.emergency.phone.mode in ("dry_run", "fake")
    app.start_replay(shifts.synthetic(n_crew=1))
    app.client.post("/api/demo/sos", json={"worker_id": "demo-1"})
    app.step(45)
    assert built == []
    with pytest.raises(Exception):
        voice_agent.ElevenLabsPhone()                           # refuses to exist without EMERGENCY_LIVE_CALLS=1


def test_A20_one_call_per_incident_and_cancel_stops_it(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=1))
    app.step(60)
    for _ in range(3):
        app.client.post("/api/demo/sos", json={"worker_id": "demo-1"})
    # The same confirmed event delivered twice (e.g. a retry) is still one incident.
    ev = {"type": "mandown_confirmed", "worker_id": "demo-1", "started_at": 1.0, "confirmed_at": 2.0}
    app.on_loop(app.emergency.handle, ev)
    app.step(40)
    assert app.phone.asked == list(TEST_NUMBERS.values())       # crew lead, safety officer, emergency: once
    app.client.post("/api/demo/sos", json={"worker_id": "demo-2"})
    assert app.client.post("/api/emergency/cancel", json={"worker_id": "demo-2"}).json()["cancelled"] is True
    app.step(40)
    assert app.phone.asked.count(TEST_NUMBERS["emergency"]) == 1    # demo-2's call never went out
    assert any(e["step"] == "cancelled" for e in app.emergency.log.entries)


def test_A21_call_script_has_facts_only_and_says_unknown():
    bare = MandownConfirmed(worker_id="w-77", started_at=1_792_000_000.0, confirmed_at=1_792_000_090.0)
    script = emergency_script(build_report(bare, 1_792_000_100.0))
    assert "Unknown" in script and "w-77" in script
    for invented in ("°C", "bpm", "Gainesville", "Deerhaven"):
        assert invented not in script
    full = MandownConfirmed(worker_id="w-1", worker_name="Maria", site_name="Deerhaven Generating Station",
                            lat=29.76, lon=-82.39, started_at=1_792_000_000.0, confirmed_at=1_792_000_090.0,
                            core_temp_c=38.6, hr_bpm=151, skin_temp_c=35.2)
    s = emergency_script(build_report(full, 1_792_000_100.0))
    assert "Maria" in s and "Deerhaven" in s and "38.6" in s and "151" in s


def test_A20_manager_ignores_a_repeated_event():
    m = EscalationManager(phone=FakePhone(allowlist=set(TEST_NUMBERS.values())), log=EscalationLog(None),
                          contacts=dict(TEST_NUMBERS), clock=lambda: 10.0)
    ev = {"type": "mandown_confirmed", "worker_id": "w", "started_at": 1.0, "confirmed_at": 2.0}
    assert m.handle(ev) is not None and m.handle(ev) is None
    assert m.handle({"type": "sos", "worker_id": "w", "pressed_at": 3.0}) is None


# ---------------------------------------------------------------------------
# 5. Site hazards
# ---------------------------------------------------------------------------
def flashes_at(site, miles, n=2, q=0, t=0.0):
    dlon = miles / (69.0 * math.cos(math.radians(site.lat)))
    return [{"t": t - 20 * i, "lat": site.lat, "lon": site.lon + dlon, "q": q} for i in range(n)]


def test_A22_lightning_rule_edges():
    from backend.hazards.lightning import site_lightning_state
    from backend.sites.sites import get_site
    site = get_site("eia-663")
    now = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
    t = now.timestamp()
    state = lambda fl, ok=now: site_lightning_state(site, fl, now, ok)["state"]  # noqa: E731
    assert state(flashes_at(site, 9.9, t=t)) == "stop_work"
    assert state(flashes_at(site, 10.1, t=t)) != "stop_work"
    assert state(flashes_at(site, 3.0, n=1, t=t)) == "unconfirmed"           # one flash isn't enough
    assert state(flashes_at(site, 3.0, q=1, t=t)) == "clear"                 # bad-quality flashes ignored
    old = flashes_at(site, 3.0, t=t - 31 * 60)                                # 31 quiet minutes
    assert state(old) != "stop_work"
    assert state(old, ok=now - timedelta(minutes=10)) == "stop_work"         # feed down: never all-clear


def test_A23_nws_extreme_heat_codes_and_patterns():
    from backend.hazards.nws import classify
    eh = classify({"event": "Something", "vtec": ["/O.NEW.KJAX.EH.W.0001.260720T1800Z-260721T0000Z/"]})
    xh = classify({"event": "Something", "vtec": ["/O.NEW.KJAX.XH.W.0001.260720T1800Z-260721T0000Z/"]})
    assert eh == xh and eh[0] == "heat"
    assert classify({"event": "Flood Warning"})[0] == "flood"
    assert classify({"event": "Red Flag Warning"})[0] == "fire"
    assert classify({"event": "Severe Thunderstorm Warning"})[0] in ("weather", "lightning")


def test_A25_hazard_index_is_0_to_10_or_none_everywhere():
    from backend.hazards import contract, picture, store
    store.set_mode("fake")
    pic = picture.hazard_picture(write_log=False)
    for status in contract.site_statuses(pic):
        idx = status["index_tomorrow"]
        assert idx is None or 0 <= idx <= 10, (status["site_id"], idx)


# ---------------------------------------------------------------------------
# 7. Server, privacy and robustness
# ---------------------------------------------------------------------------
def test_A30_bad_requests_get_4xx_not_500(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(3 * 60)
    c = app.client
    cases = [
        c.post("/spot-check", content=b"{not json", headers={"content-type": "application/json"}),
        c.post("/spot-check", json={"worker_id": "demo-1"}),
        c.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": "hot"}),
        c.post("/api/demo/threshold", json={"on": "maybe"}),
        c.post("/api/demo/hazard", json={"kind": "tornado"}),
        c.post("/api/workers/nobody/button"),
        c.get("/api/state?view=boss"),
    ]
    assert [r.status_code for r in cases if not 400 <= r.status_code < 500] == []


@pytest.mark.xfail(strict=True, reason="BUG -> session A: POST /api/demo/replay {speed: 'fast'} crashes with 500 "
                   "(float('fast') unguarded) instead of a 4xx")
def test_A30_demo_replay_bad_speed_is_4xx(live_app):
    r = live_app().client.post("/api/demo/replay", json={"speed": "fast"})
    assert 400 <= r.status_code < 500


@pytest.mark.xfail(strict=True, reason="BUG -> session A: spot checks outside 34-42 °C are accepted with 200 "
                   "(contract: 400); they're only 'rejected' as bad readings after the fact")
def test_A30_out_of_range_spot_check_is_4xx(live_app):
    app = live_app()
    app.start_replay(shifts.synthetic(n_crew=0, fall_worker=None))
    app.step(3 * 60)
    r = app.client.post("/spot-check", json={"worker_id": "demo-1", "thermometer_c": 55.0})
    assert 400 <= r.status_code < 500


def test_A31_cors_only_allows_the_frontend_origins(live_app):
    c = live_app().client
    ok = c.get("/api/workers", headers={"Origin": "http://localhost:3000"})
    bad = c.get("/api/workers", headers={"Origin": "http://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in bad.headers


def run_signature(live_app, speed_change: bool):
    app = live_app(clock=StepClock(start=1_792_000_000.0))
    app.start_replay(shifts.synthetic(n_crew=2))
    app.step(30 * 60)
    if speed_change:
        app.client.post("/api/replay/speed", json={"speed": 60})
    app.step(30 * 60)
    sig = [(e["type"], e.get("worker_id"), e.get("minute"), e.get("level"), e.get("line"), e.get("core_temp"))
           for e in app.events if e["type"] in ("estimate", "heat_alert", "band_command", "mandown_check",
                                                "mandown_confirmed")]
    live_app.stop(app)
    return sig


def test_A32_replay_is_deterministic_and_speed_changes_skip_nothing(live_app):
    a = run_signature(live_app, False)
    b = run_signature(live_app, True)
    assert a == b
    minutes = [m for t, w, m, *_ in a if t == "estimate" and w == "demo-1"]
    assert minutes == list(range(minutes[0], minutes[0] + len(minutes)))


def test_A33_six_workers_full_shift_fast(live_app):
    import time
    app = live_app()
    plan = shifts.synthetic(minutes=480, n_crew=5)
    app.start_replay(plan)
    t0 = time.monotonic()
    app.step(480 * 60, dt=5.0)
    assert time.monotonic() - t0 < 120
    for wid in app.replay.workers:
        assert len(app.of_type("estimate", wid)) >= 470
        assert len(app.hub.pipelines[wid].estimates) <= 600       # memory stays bounded (HISTORY_KEEP)
