"""Bluetooth heart rate: packet parsing, device picking, reconnect, status, fusion. No Bluetooth needed."""

import asyncio
import socket

import pytest

from backend import config
from backend.alerts.band_output import MultiBand
from backend.app.hub import Hub
from backend.app.pipeline import WorkerPipeline
from backend.app.schemas import Reading
from backend.ingest import ble_hr
from backend.ingest.ble_hr import BleHeartRate, matches, parse_hr_measurement, usable
from backend.ingest.fake_ble import PACKETS, FakeBleScanner, device, fake_client


def test_parse_real_example_packets():
    assert parse_hr_measurement(PACKETS["uint8"]).hr == 72
    m = parse_hr_measurement(PACKETS["strap_rr"])
    assert m.hr == 75 and m.contact is True and m.rr_s == pytest.approx([797 / 1024])
    assert parse_hr_measurement(PACKETS["uint16"]).hr == 90
    m = parse_hr_measurement(PACKETS["energy_rr"])
    assert m.hr == 80 and m.energy_kj == 16 and m.rr_s == pytest.approx([1.0, 0.75])
    m = parse_hr_measurement(PACKETS["no_contact"])
    assert m.contact is False and not usable(m)            # strap off the skin: don't trust it
    assert parse_hr_measurement(PACKETS["short"]) is None
    assert parse_hr_measurement(b"\x00") is None
    assert usable(parse_hr_measurement(PACKETS["uint8"]))


def test_device_matching():
    hc, hc_adv = device("HeartCast")
    tv, tv_adv = device("Living Room TV", address="11:22", heart_rate=False)
    assert matches(hc, hc_adv, None) and not matches(tv, tv_adv, None)     # any 0x180D device
    assert matches(hc, hc_adv, "heartcast")                                # name part, any case
    assert not matches(hc, hc_adv, "polar")
    assert matches(tv, tv_adv, "11:22")                                     # exact address


def collect(hub):
    readings, links = [], []
    hub.add_listener(lambda e: (readings if e["type"] == "reading" else links if e["type"] == "hr_link" else []).append(e))
    return readings, links


def test_connect_stream_then_lost_status():
    hub = Hub(band=MultiBand([]))
    readings, links = collect(hub)
    pkts = [PACKETS["uint8"], PACKETS["no_contact"], PACKETS["strap_rr"]]
    reader = BleHeartRate(hub, "w1", scanner=FakeBleScanner([device("HeartCast")]), client_cls=fake_client(pkts))
    asyncio.run(reader.run_once())
    assert [r["hr"] for r in readings] == [72.0, 75.0]                     # off-skin packet skipped
    assert all(r["source"] == "ble" and r["worker_id"] == "w1" for r in readings)
    assert [l["state"] for l in links] == ["searching", "connected", "lost"]
    assert links[1]["device"] == "HeartCast"
    assert hub.pipelines["w1"].last_reading["hr_source"] == "ble"


def test_picks_named_device_and_reconnects_after_phone_walks_away():
    hub = Hub(band=MultiBand([]))
    readings, links = collect(hub)
    nearby = [device("Polar H10", address="P1"), device("HeartCast iPhone", address="H1")]
    client = fake_client([PACKETS["uint8"]] * 3, drop_after=2)
    reader = BleHeartRate(hub, "w1", wanted="HeartCast", scanner=FakeBleScanner(nearby), client_cls=client)

    async def go():
        task = asyncio.create_task(reader.run())
        while len(client.instances) < 2:                 # dropped once, connected again
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(go())
    assert all(c.dev.address == "H1" for c in client.instances)
    states = [l["state"] for l in links]
    assert states.count("connected") >= 2 and "lost" in states
    assert len(readings) >= 4


def test_stale_link_reports_and_reconnects():
    hub = Hub(band=MultiBand([]))
    readings, links = collect(hub)
    client = fake_client([PACKETS["uint8"]], then_silent=True)   # stays connected, goes quiet
    reader = BleHeartRate(hub, "w1", scanner=FakeBleScanner([device()]), client_cls=client, stale_s=0.1)
    asyncio.run(asyncio.wait_for(reader.run_once(), timeout=5))
    assert [l["state"] for l in links] == ["searching", "connected", "stale", "lost"]


def test_nothing_around_keeps_searching_without_crashing():
    hub = Hub(band=MultiBand([]))
    _, links = collect(hub)
    scanner = FakeBleScanner([device("Speaker", heart_rate=False)])
    reader = BleHeartRate(hub, "w1", scanner=scanner, client_cls=fake_client([]))

    async def go():
        task = asyncio.create_task(reader.run())
        while scanner.scans < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(go())
    assert [l["state"] for l in links] == ["searching"]           # one status, not one per scan


def test_ble_and_pulsoid_are_fused():
    from backend.models.kalman import ECTempKalman
    p = WorkerPipeline("w1")
    for i in range(24):                                          # 2 minutes, both sources
        p.handle(Reading(worker_id="w1", timestamp=i * 5.0, hr=140, source="watch"))      # Pulsoid
        p.handle(Reading(worker_id="w1", timestamp=i * 5.0 + 1, hr=130, source="ble"))    # HeartCast / strap
    est = p.estimates[0]
    assert est["hr_sources"] == ["watch", "ble"] and est["hr_ble"] == 130
    expected = ECTempKalman().step_fused([(140.0, config.KALMAN_SIGMA_WATCH), (130.0, config.KALMAN_SIGMA_WATCH)])
    assert est["kalman"] == pytest.approx(expected.ct, abs=1e-3)
    assert est["hr"] == pytest.approx(135.0, abs=0.01)          # equal sigmas -> plain average


def test_live_mode_starts_bluetooth_automatically(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.app import main

    started = []

    class Recorder:
        def __init__(self, hub, worker_id, wanted=None):
            started.append((worker_id, wanted))

        async def run(self):
            await asyncio.sleep(3600)

    monkeypatch.setattr(ble_hr, "BleHelper", Recorder)
    monkeypatch.setattr(ble_hr, "safe_to_scan", lambda: True)
    monkeypatch.setattr(config, "PULSOID_TOKEN", "")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(main, "SETTINGS", main.parse_args(
        ["--source", "live", "--serial", "", "--udp-port", str(port), "--ble-device", "HeartCast", "--db", "",
         "--no-forecaster", "--hazards", "off", "--no-planner", "--emergency-log", ""]))
    with TestClient(main.app):
        pass
    assert started == [(config.WORKER_ID, "HeartCast")]


def test_no_real_bluetooth_inside_the_test_suite():
    assert ble_hr.safe_to_scan() is False


def test_helper_process_lines_become_readings_and_a_crash_is_reported():
    import sys
    script = ("import json,sys\n"
              "print(json.dumps({'kind':'link','state':'connected','device':'HeartCast','message':'receiving'}))\n"
              "print('stray debug text')\n"
              "print(json.dumps({'kind':'hr','hr':84}))\n"
              "print(json.dumps({'kind':'hr','hr':86}))\n"
              "sys.stdout.flush(); sys.exit(-6 % 256)\n")
    hub = Hub(band=MultiBand([]))
    readings, links = collect(hub)
    helper = ble_hr.BleHelper(hub, "w1", cmd=[sys.executable, "-c", script], restart_s=60)

    async def go():
        task = asyncio.create_task(helper.run())
        while not any(l["state"] == "lost" for l in links):
            await asyncio.sleep(0.02)
        task.cancel()

    asyncio.run(asyncio.wait_for(go(), timeout=20))
    assert [r["hr"] for r in readings] == [84.0, 86.0] and all(r["source"] == "ble" for r in readings)
    assert [l["state"] for l in links] == ["connected", "lost"]
    assert links[0]["device"] == "HeartCast" and "exit code" in links[1]["message"]
