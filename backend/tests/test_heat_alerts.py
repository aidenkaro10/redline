"""Heat alert state machine + band router."""

from backend import config
from backend.alerts.heat import CLEAR, RED, YELLOW, HeatAlerter, HeatInputs
from backend.alerts.router import BandRouter


def levels(alerter, values, rule="kalman"):
    """Feed one estimate per minute; return the level after each."""
    out = []
    for i, v in enumerate(values):
        x = HeatInputs(v) if rule == "kalman" else HeatInputs(v[0], v[1], v[2])
        alerter.update(i * 60.0, x)
        out.append(alerter.level)
    return out


def test_kalman_rule_yellow_then_red():
    a = HeatAlerter()
    assert levels(a, [37.5, 38.0, 38.3, 38.5]) == [CLEAR, YELLOW, YELLOW, RED]


def test_hysteresis_no_flicker():
    a = HeatAlerter()
    # Hovering just under 38.0 after crossing it keeps yellow on.
    seq = [38.0, 37.95, 38.01, 37.92, 37.89]
    assert levels(a, seq) == [YELLOW, YELLOW, YELLOW, YELLOW, CLEAR]


def test_red_drops_to_yellow_with_margin():
    a = HeatAlerter()
    assert levels(a, [38.5, 38.45, 38.39]) == [RED, RED, YELLOW]


def test_forecast_rule():
    a = HeatAlerter()
    # (estimate, 10-min p90, 20-min p90)
    seq = [(37.4, 37.8, 37.9), (37.5, 37.9, 38.0), (37.6, 38.5, 38.7), (38.5, 38.0, 38.0)]
    assert levels(a, seq, rule="forecast") == [CLEAR, YELLOW, RED, RED]
    ev = a.update(300, HeatInputs(37.0, 37.2, 37.3))
    assert ev[0]["level"] == CLEAR and ev[0]["rule"] == "forecast"


def test_events_only_on_change():
    a = HeatAlerter()
    assert a.update(0, HeatInputs(38.1))[0]["level"] == YELLOW
    assert a.update(60, HeatInputs(38.2)) == []


def test_demo_thresholds_toggle():
    a = HeatAlerter()
    assert a.update(0, HeatInputs(37.4)) == []
    a.set_demo(True)
    ev = a.update(60, HeatInputs(37.4))
    assert ev[0]["level"] == YELLOW and ev[0]["threshold"] == config.DEMO_HEAT_WARNING_C
    assert a.update(120, HeatInputs(37.5))[0]["level"] == RED


def test_red_unacknowledged_fires_once():
    a = HeatAlerter()
    a.update(0, HeatInputs(38.6))
    assert a.tick(config.HEAT_RED_ACK_TIMEOUT_S - 1) == []
    ev = a.tick(config.HEAT_RED_ACK_TIMEOUT_S)
    assert ev[0]["type"] == "red_unacknowledged"
    assert a.tick(config.HEAT_RED_ACK_TIMEOUT_S + 30) == []


def test_button_ack_stops_timeout():
    a = HeatAlerter()
    a.update(0, HeatInputs(38.6))
    assert a.acknowledge(20)[0]["type"] == "heat_ack"
    assert a.tick(config.HEAT_RED_ACK_TIMEOUT_S + 5) == []
    assert a.acknowledge(30) == []           # already acknowledged


def test_ack_resets_for_new_red_episode():
    a = HeatAlerter()
    a.update(0, HeatInputs(38.6))
    a.acknowledge(10)
    a.update(60, HeatInputs(38.2))           # back to yellow
    a.update(120, HeatInputs(38.6))          # red again: needs a new press
    assert a.tick(120 + config.HEAT_RED_ACK_TIMEOUT_S)[0]["type"] == "red_unacknowledged"


def test_router_priority_and_only_on_change():
    r = BandRouter("w1")
    assert r.set("HEAT_YELLOW", True, 0)[0]["line"] == "BUZZ:HEAT_YELLOW"
    assert r.set("FLOOD_FIRE", True, 1) == []              # lower priority: no change
    assert r.set("HEAT_RED", True, 2)[0]["pattern"] == "HEAT_RED"
    assert r.set("MANDOWN", True, 3)[0]["pattern"] == "MANDOWN"
    assert r.set("MANDOWN", False, 4)[0]["pattern"] == "HEAT_RED"
    r.set("HEAT_RED", False, 5)
    r.set("HEAT_YELLOW", False, 6)
    assert r.set("FLOOD_FIRE", False, 7)[0]["line"] == "BUZZ:OFF"
