"""
Heart rate over Bluetooth (BLE). Works with NO internet (docs/decisions.md, entry 1).

Any device that acts as a standard Bluetooth heart rate sensor works:
  - the HeartCast app on an iPhone, relaying the Apple Watch (our main setup)
  - a Bluetooth chest strap (Polar, Wahoo, Garmin HRM ...)
  - a Garmin watch with "broadcast heart rate" turned on

How it works:
  1. Scan for a device advertising the Heart Rate Service (0x180D). If BLE_HR_DEVICE
     is set in .env, only a device whose name contains it (or whose address equals it)
     is used; otherwise the first heart rate device found (its name is logged).
  2. Connect and subscribe to Heart Rate Measurement (0x2A37) notifications.
  3. Each notification -> a Reading(hr=..., source="ble") stamped with the LAPTOP clock.
  4. If the phone wanders off or the strap stops talking: an "hr_link" status event
     (state "lost" or "stale"), then keep trying to reconnect (1 s, 2 s, 4 s ... 15 s).

macOS asks once for Bluetooth permission for the app running Python (Terminal, iTerm,
VS Code ...). Say yes, or nothing will be found. System Settings > Privacy & Security >
Bluetooth if you said no.

In the server, Bluetooth runs in a small HELPER PROCESS (BleHelper below) that prints
JSON lines. If macOS refuses Bluetooth for the app that started Python, it kills that
process on the spot; with the helper, only the helper dies (and is restarted with a
clear status message), never the server and its alerts.

Check it without the server:
    python -m backend.ingest.ble_hr --scan      list nearby heart rate devices
    python -m backend.ingest.ble_hr             print heart rate as it arrives
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

from backend import config
from backend.app.schemas import Reading

log = logging.getLogger(__name__)

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"         # Heart Rate Service (0x180D)
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"     # Heart Rate Measurement (0x2A37)
SCAN_S = 8.0                 # how long one scan looks for a device
MAX_RETRY_S = 15.0           # longest pause between reconnect attempts


# ---------------------------------------------------------------------------
# The packet (Bluetooth SIG Heart Rate Measurement characteristic)
# ---------------------------------------------------------------------------
@dataclass
class HrMeasurement:
    hr: int                              # beats per minute
    contact: bool | None = None          # sensor touching skin? None = sensor doesn't report it
    energy_kj: int | None = None         # energy expended (rarely sent)
    rr_s: list[float] = field(default_factory=list)   # beat-to-beat intervals, seconds


def parse_hr_measurement(data: bytes) -> HrMeasurement | None:
    """
    Byte 0 is a flags byte:
      bit 0   heart rate is 16-bit (1) or 8-bit (0)
      bit 1   skin contact detected        } only meaningful
      bit 2   skin contact is supported    } together
      bit 3   energy expended present (16-bit, kJ)
      bit 4   RR intervals present (16-bit each, units of 1/1024 s)
    All numbers are little-endian. Returns None for a packet that's too short.
    """
    data = bytes(data)
    if len(data) < 2:
        return None
    flags = data[0]
    i = 1
    if flags & 0x01:
        if len(data) < 3:
            return None
        hr = int.from_bytes(data[1:3], "little")
        i = 3
    else:
        hr = data[1]
        i = 2
    contact = bool(flags & 0x02) if flags & 0x04 else None
    energy = None
    if flags & 0x08 and len(data) >= i + 2:
        energy = int.from_bytes(data[i:i + 2], "little")
        i += 2
    rr = []
    if flags & 0x10:
        while len(data) >= i + 2:
            rr.append(int.from_bytes(data[i:i + 2], "little") / 1024.0)
            i += 2
    return HrMeasurement(hr=hr, contact=contact, energy_kj=energy, rr_s=rr)


def usable(m: HrMeasurement | None) -> bool:
    """A heart rate we trust: plausible, and not from a strap that says it's off the skin."""
    return m is not None and m.contact is not False and 25 <= m.hr <= 240


# ---------------------------------------------------------------------------
# Picking the device
# ---------------------------------------------------------------------------
def matches(device, adv, wanted: str | None) -> bool:
    """
    wanted (BLE_HR_DEVICE): part of the device name, or its exact address. macOS shows a
    UUID instead of a MAC address; both work. No wanted -> any heart rate service device.
    """
    name = (getattr(adv, "local_name", None) or getattr(device, "name", None) or "")
    if wanted:
        w = wanted.strip().lower()
        return w in name.lower() or w == str(getattr(device, "address", "")).lower()
    uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
    return HR_SERVICE in uuids


# ---------------------------------------------------------------------------
# The live client
# ---------------------------------------------------------------------------
class BleHeartRate:
    """
    Keeps one Bluetooth heart rate device connected and feeds the hub.
    scanner / client_cls default to bleak's; tests pass the fakes from backend/ingest/fake_ble.py.
    """

    def __init__(self, hub, worker_id: str, wanted: str | None = None, scanner=None, client_cls=None,
                 clock=time.time, stale_s: float | None = None, scan_s: float = SCAN_S):
        if scanner is None or client_cls is None:
            from bleak import BleakClient, BleakScanner      # imported here so tests need no Bluetooth
            scanner = scanner or BleakScanner
            client_cls = client_cls or BleakClient
        self.hub, self.worker_id, self.wanted = hub, worker_id, wanted
        self.scanner, self.client_cls, self.clock = scanner, client_cls, clock
        self.stale_s = config.BLE_HR_STALE_S if stale_s is None else stale_s
        self.scan_s = scan_s
        self.state = "off"
        self.device_name = None
        self.device_address = None
        self.last_packet_t: float | None = None
        self.packets = 0
        self._dropped = None

    # -- status -----------------------------------------------------------------
    def _status(self, state: str, message: str) -> None:
        """One "hr_link" event per change: searching / connected / stale / lost."""
        if state == self.state:
            return
        self.state = state
        log.info("Bluetooth heart rate: %s", message)
        self.hub.dispatch([{
            "type": "hr_link", "timestamp": self.clock(), "worker_id": self.worker_id, "source": "ble",
            "state": state, "device": self.device_name, "address": self.device_address, "message": message,
        }])

    # -- packets ------------------------------------------------------------------
    def on_packet(self, _char, data) -> None:
        t = self.clock()                                   # laptop timestamp on arrival
        m = parse_hr_measurement(data)
        if not usable(m):
            return
        self.packets += 1
        self.last_packet_t = t
        if self.state != "connected":
            self._status("connected", f"receiving from {self.device_name or self.device_address}")
        self.hub.handle_input(Reading(worker_id=self.worker_id, timestamp=t, hr=float(m.hr), source="ble"),
                              source="ble")

    # -- connect loop ----------------------------------------------------------------
    async def find(self):
        """The device to use, or None if nothing matching is around."""
        result = await self.scanner.find_device_by_filter(
            lambda d, adv: matches(d, adv, self.wanted), timeout=self.scan_s)
        if result is not None:
            self.device_name = getattr(result, "name", None)
            self.device_address = getattr(result, "address", None)
        return result

    async def run_once(self) -> None:
        """Find, connect, stream until the link drops (or goes silent)."""
        self._status("searching", "looking for a heart rate device"
                     + (f" named {self.wanted!r}" if self.wanted else " (any 0x180D device)"))
        device = await self.find()
        if device is None:
            return
        loop = asyncio.get_running_loop()
        self._dropped = asyncio.Event()
        # bleak may call these from its own thread: hop onto the event loop before touching the hub.
        on_drop = lambda _client: loop.call_soon_threadsafe(self._dropped.set)
        on_data = lambda char, data: loop.call_soon_threadsafe(self.on_packet, char, bytes(data))
        async with self.client_cls(device, disconnected_callback=on_drop) as client:
            await client.start_notify(HR_MEASUREMENT, on_data)
            log.info("Connected to %s (%s)", self.device_name, self.device_address)
            connected_at = self.clock()
            while not self._dropped.is_set():
                await asyncio.sleep(0.5)
                quiet_since = self.last_packet_t if self.last_packet_t is not None else connected_at
                if self.clock() - quiet_since > self.stale_s:
                    # Still "connected" but nothing for a while (watch screen off, app in background).
                    self._status("stale", f"no heart rate from {self.device_name} for {self.stale_s:.0f} s")
                    if self.clock() - quiet_since > 3 * self.stale_s:
                        break                               # give up on this link and reconnect
        self._status("lost", f"lost {self.device_name or 'the device'}; reconnecting")

    async def run(self) -> None:
        """Forever: keep a heart rate device connected. Cancel the task to stop."""
        pause = 1.0
        while True:
            try:
                await self.run_once()
                pause = 1.0 if self.packets else min(pause * 2, MAX_RETRY_S)
            except asyncio.CancelledError:
                raise
            except Exception as e:                          # adapter off, permission denied, device gone ...
                self._status("lost", f"Bluetooth problem ({e}); retrying")
                pause = min(pause * 2, MAX_RETRY_S)
            await asyncio.sleep(pause)


# ---------------------------------------------------------------------------
# The server side: run the reader in a helper process
# ---------------------------------------------------------------------------
class BleHelper:
    """
    Starts `python -m backend.ingest.ble_hr --json` and turns its lines into Readings
    (laptop timestamp on arrival, source "ble") and "hr_link" status events. If the helper
    exits (Bluetooth refused, adapter off, crash), it says so and starts it again.
    """

    def __init__(self, hub, worker_id: str, wanted: str | None = None, cmd: list[str] | None = None,
                 clock=time.time, restart_s: float = 5.0, max_restart_s: float = 60.0):
        self.hub, self.worker_id, self.clock = hub, worker_id, clock
        self.restart_s, self.max_restart_s = restart_s, max_restart_s
        self.cmd = cmd or helper_command(wanted)
        self.state = "off"
        self.device = self.address = None
        self.readings = 0

    def _status(self, state: str, message: str, device=None, address=None) -> None:
        self.device = device or self.device
        self.address = address or self.address
        if state == self.state:
            return
        self.state = state
        log.info("Bluetooth heart rate: %s", message)
        self.hub.dispatch([{"type": "hr_link", "timestamp": self.clock(), "worker_id": self.worker_id,
                            "source": "ble", "state": state, "device": self.device, "address": self.address,
                            "message": message}])

    def handle_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except ValueError:
            return                                        # not ours (a stray print)
        if msg.get("kind") == "hr" and isinstance(msg.get("hr"), (int, float)):
            self.readings += 1
            self.hub.handle_input(Reading(worker_id=self.worker_id, timestamp=self.clock(), hr=float(msg["hr"]),
                                          source="ble"), source="ble")
        elif msg.get("kind") == "link" and msg.get("state"):
            self._status(msg["state"], msg.get("message") or msg["state"], msg.get("device"), msg.get("address"))

    async def run(self) -> None:
        """Forever: keep the helper running. Cancel the task to stop it (and the helper)."""
        pause = self.restart_s
        while True:
            before = self.readings
            proc = await asyncio.create_subprocess_exec(
                *self.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    self.handle_line(raw.decode(errors="replace"))
                code = await proc.wait()
            except asyncio.CancelledError:
                if proc.returncode is None:
                    proc.kill()
                raise
            how = f"killed by signal {-code}" if code < 0 else f"exit code {code}"
            hint = ("" if code == 0 else
                    ". If this repeats: System Settings > Privacy & Security > Bluetooth, allow the app running "
                    "Python (Terminal), or start with --no-ble")
            # Got heart rate this time -> retry soon; nothing at all -> back off (5 s, 10 s ... 60 s).
            pause = self.restart_s if self.readings > before else min(pause * 2, self.max_restart_s)
            self._status("lost", f"Bluetooth heart rate unavailable (helper {how}){hint}; "
                                 f"trying again in {pause:.0f} s")
            self.state = "restarting"                     # so the next "lost" is reported again
            await asyncio.sleep(pause)


def helper_command(wanted: str | None = None) -> list[str]:
    """The helper process the server runs (tests swap this for a fake)."""
    return [sys.executable, "-m", "backend.ingest.ble_hr", "--json"] + (["--device", wanted] if wanted else [])


def safe_to_scan() -> bool:
    """
    Never touch real Bluetooth inside the test suite: no adapter in CI, and on a Mac a process
    without Bluetooth permission is killed outright. PLANIT_BLE=off turns it off anywhere.
    """
    return "PYTEST_CURRENT_TEST" not in os.environ and os.getenv("PLANIT_BLE", "on").lower() != "off"


def available() -> bool:
    """True if bleak is installed. Looks it up WITHOUT importing it (importing loads CoreBluetooth)."""
    import importlib.util
    return importlib.util.find_spec("bleak") is not None


# ---------------------------------------------------------------------------
# Command line check: is the watch coming through?
# ---------------------------------------------------------------------------
async def _scan() -> None:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=SCAN_S, return_adv=True)
    rows = [(d, a) for d, a in found.values() if HR_SERVICE in [u.lower() for u in (a.service_uuids or [])]]
    if not rows:
        print("No heart rate devices found. Is HeartCast running (or the strap on)? "
              "Did macOS allow Bluetooth for this app?")
    for d, a in rows:
        print(f"{a.local_name or d.name or '(no name)'}  address={d.address}  signal={a.rssi} dBm")


class _JsonHub:
    """Stand-in hub for the helper process: one JSON line per reading or status change."""

    def handle_input(self, reading, source="ble"):
        print(json.dumps({"kind": "hr", "hr": reading.hr}), flush=True)

    def dispatch(self, events):
        for e in events:
            print(json.dumps({"kind": "link", **{k: e.get(k) for k in ("state", "device", "address", "message")}}),
                  flush=True)


class _PrintHub:
    """Stand-in hub for the command line: prints readings and status."""

    def handle_input(self, reading, source="ble"):
        print(f"{time.strftime('%H:%M:%S')}  HR {reading.hr:.0f} bpm", flush=True)

    def dispatch(self, events):
        for e in events:
            print(f"{time.strftime('%H:%M:%S')}  [{e['state']}] {e['message']}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bluetooth heart rate check")
    ap.add_argument("--scan", action="store_true", help="list nearby heart rate devices and exit")
    ap.add_argument("--device", default=config.BLE_HR_DEVICE or None, help="name part or address (default: BLE_HR_DEVICE)")
    ap.add_argument("--json", action="store_true", help="helper mode for the server: JSON lines on stdout")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        if a.scan:
            asyncio.run(_scan())
        else:
            hub = _JsonHub() if a.json else _PrintHub()
            asyncio.run(BleHeartRate(hub, "check", wanted=a.device).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
