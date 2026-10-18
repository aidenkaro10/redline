"""
The band over Bluetooth LE (no cable, no WiFi needed).

The band advertises as "Redline Band" with the Nordic UART Service (NUS):
    service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    TX       6E400003-...  band -> laptop, NOTIFY: the same text lines as USB serial
    RX       6E400002-...  laptop -> band, WRITE: "BUZZ:<PATTERN>\\n"

A notification can hold several lines, or only part of one, so bytes are buffered and
split on "\\n" (LineAssembler). Each complete line is stamped with the laptop clock the
moment it's complete, then parsed by the shared parser (schemas.events_from_line) and
handed to the hub exactly like the USB and WiFi readers do.

Like the Bluetooth heart rate reader (ble_hr.py), the Bluetooth part runs in a small
HELPER PROCESS:
    server  ── BleBandHelper ── starts ──> python -m backend.ingest.ble_band --json
            <── stdout: {"kind": "line", "t": ..., "line": "ACC:0.98,0.02,0.11"}
            <── stdout: {"kind": "link", "state": "connected", "message": ...}
            ──> stdin:  {"write": "BUZZ:HEAT_RED"}
If macOS refuses Bluetooth it kills the helper, never the server; the server says so and
restarts it with a growing pause. The heart rate helper is a separate process, so either
one can fail alone.

Status shows in the UI as "band_link" events (and log lines): "band connected over
Bluetooth", "band disconnected, reconnecting".

Check it without the server:
    python -m backend.ingest.ble_band --scan       list nearby Redline bands
    python -m backend.ingest.ble_band              print the band's lines as they arrive
    python -m backend.ingest.ble_band --send BUZZ:HEAT_RED     send one command, then print lines
"""

import argparse
import asyncio
import json
import logging
import sys
import time

from backend import config
from backend.alerts.band_output import BandOutput
from backend.app.schemas import events_from_line
from backend.ingest.ble_hr import available, safe_to_scan  # noqa: F401  (re-exported: same rules)

log = logging.getLogger(__name__)

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"     # Nordic UART Service
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"          # band -> laptop (notify)
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"          # laptop -> band (write)
SCAN_S = 8.0                 # how long one scan looks for the band
MAX_RETRY_S = 15.0           # longest pause between reconnect attempts
OUTBOX_MAX = 5               # commands waiting for the band (the newest one is what matters)
MAX_LINE_BYTES = 4096        # a "line" longer than this without "\n" is junk: thrown away
STATUS_TEXT = {
    "searching": "looking for the band over Bluetooth",
    "connected": "band connected over Bluetooth",
    "lost": "band disconnected, reconnecting",
}


# ---------------------------------------------------------------------------
# Bytes -> lines
# ---------------------------------------------------------------------------
class LineAssembler:
    """
    Joins notification chunks into whole lines. feed(bytes) returns the lines completed
    by this chunk (without "\\n" or "\\r"). A half line waits for the next chunk.
    Works on bytes, so a UTF-8 character split across two notifications is fine.
    """

    def __init__(self, max_bytes: int = MAX_LINE_BYTES):
        self.buf = b""
        self.max_bytes = max_bytes
        self.dropped = 0                    # junk "lines" thrown away for being too long

    def feed(self, chunk: bytes) -> list[str]:
        self.buf += bytes(chunk)
        *lines, self.buf = self.buf.split(b"\n")
        if len(self.buf) > self.max_bytes:  # no newline for ages: noise, don't grow forever
            self.buf = b""
            self.dropped += 1
        out = []
        for raw in lines:
            text = raw.decode("utf-8", errors="replace").strip("\r")
            if len(raw) > self.max_bytes:
                self.dropped += 1
                continue
            if text.strip():
                out.append(text)
        return out

    def reset(self) -> None:
        """A new connection: a half line from the old one must not glue onto the new one."""
        self.buf = b""


def matches(device, adv, wanted: str | None) -> bool:
    """
    wanted (BLE_BAND_DEVICE): part of the name, or the exact address (macOS shows a UUID).
    Nothing set -> a device named like BLE_BAND_NAME ("Redline Band").
    """
    name = (getattr(adv, "local_name", None) or getattr(device, "name", None) or "")
    w = (wanted or config.BLE_BAND_NAME).strip().lower()
    return w in name.lower() or w == str(getattr(device, "address", "")).lower()


# ---------------------------------------------------------------------------
# The Bluetooth link (runs inside the helper process, or directly in tests)
# ---------------------------------------------------------------------------
class BleBandLink:
    """
    Keeps the band connected. For every complete line: sink.on_line(line, t). For every
    status change: sink.on_status(state, message, device, address). write(line) queues a
    command for the band's RX. scanner / client_cls default to bleak's; tests pass fakes.
    """

    def __init__(self, sink, wanted: str | None = None, scanner=None, client_cls=None,
                 clock=time.time, scan_s: float = SCAN_S):
        if scanner is None or client_cls is None:
            from bleak import BleakClient, BleakScanner      # imported here so tests need no Bluetooth
            scanner = scanner or BleakScanner
            client_cls = client_cls or BleakClient
        self.sink, self.wanted = sink, wanted
        self.scanner, self.client_cls, self.clock = scanner, client_cls, clock
        self.scan_s = scan_s
        self.lines = LineAssembler()
        self.state = "off"
        self.device_name = self.device_address = None
        self.received = 0                                   # lines received, ever
        self.outbox: asyncio.Queue = asyncio.Queue(maxsize=OUTBOX_MAX)
        self._dropped: asyncio.Event | None = None

    def _status(self, state: str, message: str | None = None) -> None:
        if state == self.state:
            return
        self.state = state
        message = message or STATUS_TEXT.get(state, state)
        log.info("Bluetooth band: %s", message)
        self.sink.on_status(state, message, self.device_name, self.device_address)

    def on_data(self, _char, data) -> None:
        """A notification from TX: buffer, split, stamp each complete line now."""
        for line in self.lines.feed(bytes(data)):
            self.received += 1
            self.sink.on_line(line, self.clock())

    def write(self, line: str) -> None:
        """Queue one command for the band (sent once connected). Only the latest few are kept."""
        if self.outbox.full():
            self.outbox.get_nowait()          # band away for a while: the oldest command is stale anyway
        self.outbox.put_nowait(line)

    async def find(self):
        result = await self.scanner.find_device_by_filter(
            lambda d, adv: matches(d, adv, self.wanted), timeout=self.scan_s)
        if result is not None:
            self.device_name = getattr(result, "name", None)
            self.device_address = getattr(result, "address", None)
        return result

    async def run_once(self) -> None:
        """Find, connect, stream lines and send commands until the link drops."""
        self._status("searching", STATUS_TEXT["searching"]
                     + (f" ({self.wanted!r})" if self.wanted else f" ({config.BLE_BAND_NAME!r})"))
        device = await self.find()
        if device is None:
            return
        loop = asyncio.get_running_loop()
        self._dropped = asyncio.Event()
        self.lines.reset()
        # bleak may call these from its own thread: hop onto the event loop first.
        on_drop = lambda _client: loop.call_soon_threadsafe(self._dropped.set)      # noqa: E731
        on_data = lambda char, data: loop.call_soon_threadsafe(self.on_data, char, bytes(data))  # noqa: E731
        async with self.client_cls(device, disconnected_callback=on_drop) as client:
            await client.start_notify(NUS_TX, on_data)
            self._status("connected", f"{STATUS_TEXT['connected']} ({self.device_name or self.device_address})")
            while not self._dropped.is_set():
                try:
                    line = await asyncio.wait_for(self.outbox.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                data = (line.rstrip("\n") + "\n").encode()
                await client.write_gatt_char(NUS_RX, data, response=False)
        self._status("lost")

    async def run(self) -> None:
        """Forever: keep the band connected, reconnecting with a growing pause. Cancel to stop."""
        pause = 1.0
        while True:
            before = self.received
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:                       # adapter off, band out of range, permission ...
                self._status("lost", f"{STATUS_TEXT['lost']} (Bluetooth problem: {e})")
            pause = 1.0 if self.received > before else min(pause * 2, MAX_RETRY_S)
            await asyncio.sleep(pause)


# ---------------------------------------------------------------------------
# The server side: helper process + band output
# ---------------------------------------------------------------------------
class BleBand(BandOutput):
    """Band output: BUZZ commands for this worker go to the helper, which writes them to RX."""

    def __init__(self, helper: "BleBandHelper"):
        self.helper = helper

    def send(self, worker_id: str, line: str) -> None:
        if worker_id == self.helper.worker_id:
            self.helper.write(line)


class BleBandHelper:
    """
    Starts `python -m backend.ingest.ble_band --json` and feeds its lines to the hub (shared
    parser, laptop timestamps, source "ble_band"), and its status changes as "band_link"
    events. Commands go to the helper's stdin. If the helper exits, it says so and restarts.
    """

    def __init__(self, hub, worker_id: str, wanted: str | None = None, cmd: list[str] | None = None,
                 clock=time.time, restart_s: float = 5.0, max_restart_s: float = 60.0):
        self.hub, self.worker_id, self.clock = hub, worker_id, clock
        self.restart_s, self.max_restart_s = restart_s, max_restart_s
        self.cmd = cmd or helper_command(wanted)
        self.state = "off"
        self.device = self.address = None
        self.lines = 0
        self.proc = None
        self.output = BleBand(self)

    # -- band_link status -----------------------------------------------------------
    def _status(self, state: str, message: str, device=None, address=None) -> None:
        self.device = device or self.device
        self.address = address or self.address
        if state == self.state:
            return
        self.state = state
        log.info("Bluetooth band: %s", message)
        self.hub.dispatch([{"type": "band_link", "timestamp": self.clock(), "worker_id": self.worker_id,
                            "transport": "ble", "state": state, "device": self.device, "address": self.address,
                            "message": message}])

    # -- helper -> server -------------------------------------------------------------
    def handle_line(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return                                        # not ours (a stray print)
        if not isinstance(msg, dict):
            return
        if msg.get("kind") == "line" and isinstance(msg.get("line"), str):
            now = self.clock()
            t = msg.get("t")
            # The helper stamps each line when it's complete (same laptop clock). Anything odd -> now.
            t = t if isinstance(t, (int, float)) and abs(t - now) < 5 else now
            self.lines += 1
            for event in events_from_line(msg["line"], self.worker_id, t):
                self.hub.handle_input(event, source="ble_band")
        elif msg.get("kind") == "link" and msg.get("state"):
            self._status(msg["state"], msg.get("message") or STATUS_TEXT.get(msg["state"], msg["state"]),
                         msg.get("device"), msg.get("address"))

    # -- server -> helper -------------------------------------------------------------
    def write(self, line: str) -> None:
        """Send a command line to the band (via the helper). Dropped quietly if the helper isn't up."""
        proc = self.proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return
        try:
            proc.stdin.write((json.dumps({"write": line}) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            pass

    async def run(self) -> None:
        """Forever: keep the helper running. Cancel the task to stop it (and the helper)."""
        pause = self.restart_s
        while True:
            before = self.lines
            self.proc = proc = await asyncio.create_subprocess_exec(
                *self.cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
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
                    "Python (Terminal), or use the USB cable (--band usb)")
            pause = self.restart_s if self.lines > before else min(pause * 2, self.max_restart_s)
            self._status("lost", f"{STATUS_TEXT['lost']} (helper {how}{hint}); trying again in {pause:.0f} s")
            self.state = "restarting"                     # so the next "lost" is reported again
            await asyncio.sleep(pause)


def helper_command(wanted: str | None = None) -> list[str]:
    """The helper process the server runs (tests swap this for a fake)."""
    return [sys.executable, "-m", "backend.ingest.ble_band", "--json"] + (["--device", wanted] if wanted else [])


# ---------------------------------------------------------------------------
# Helper / command line
# ---------------------------------------------------------------------------
class _JsonSink:
    """Helper mode: one JSON line per band line or status change, on stdout."""

    def on_line(self, line: str, t: float) -> None:
        print(json.dumps({"kind": "line", "t": t, "line": line}), flush=True)

    def on_status(self, state, message, device, address) -> None:
        print(json.dumps({"kind": "link", "state": state, "message": message, "device": device,
                          "address": address}), flush=True)


class _PrintSink:
    """Command line check: print what the band says."""

    def on_line(self, line: str, t: float) -> None:
        print(f"{time.strftime('%H:%M:%S')}  {line}", flush=True)

    def on_status(self, state, message, device, address) -> None:
        print(f"{time.strftime('%H:%M:%S')}  [{state}] {message}", flush=True)


async def _read_commands(link: BleBandLink) -> None:
    """Helper mode: each stdin line {"write": "BUZZ:..."} goes to the band."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while True:
        raw = await reader.readline()
        if not raw:
            raise SystemExit(0)                            # the server went away: so do we
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict) and isinstance(msg.get("write"), str):
            link.write(msg["write"])


async def _helper(wanted: str | None, json_mode: bool, send: str | None) -> None:
    link = BleBandLink(_JsonSink() if json_mode else _PrintSink(), wanted=wanted)
    if send:
        link.write(send)
    tasks = [asyncio.create_task(link.run())]
    if json_mode:
        tasks.append(asyncio.create_task(_read_commands(link)))
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    for t in done:
        t.result()


async def _scan() -> None:
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=SCAN_S, return_adv=True)
    rows = [(d, a) for d, a in found.values()
            if NUS_SERVICE in [u.lower() for u in (a.service_uuids or [])] or matches(d, a, None)]
    if not rows:
        print(f"No band found. Is it on and advertising as {config.BLE_BAND_NAME!r}? "
              "Did macOS allow Bluetooth for this app?")
    for d, a in rows:
        print(f"{a.local_name or d.name or '(no name)'}  address={d.address}  signal={a.rssi} dBm")


def main(argv=None):
    ap = argparse.ArgumentParser(description="The band over Bluetooth LE (Nordic UART)")
    ap.add_argument("--scan", action="store_true", help="list nearby bands and exit")
    ap.add_argument("--device", default=config.BLE_BAND_DEVICE or None,
                    help=f"name part or address (default: BLE_BAND_DEVICE, else {config.BLE_BAND_NAME!r})")
    ap.add_argument("--json", action="store_true", help="helper mode for the server: JSON lines on stdout")
    ap.add_argument("--send", default=None, help="send one command once connected, e.g. BUZZ:HEAT_RED")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    try:
        if a.scan:
            asyncio.run(_scan())
        else:
            asyncio.run(_helper(a.device, a.json, a.send))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
