"""
A fake Bluetooth heart rate device for tests (no Bluetooth needed, works in CI).

FakeBleScanner and FakeBleClient stand in for bleak's BleakScanner and BleakClient
inside BleHeartRate. The client plays real Heart Rate Measurement packets and can
pretend the phone walked away (disconnect) or went quiet (stops sending).
"""

import asyncio
from types import SimpleNamespace

from backend.ingest.ble_hr import HR_SERVICE

# Real-world example packets (flags byte first, little-endian).
PACKETS = {
    "uint8": bytes([0x00, 72]),                         # 72 bpm, nothing else (HeartCast / Garmin style)
    "strap_rr": bytes([0x16, 75, 0x1D, 0x03]),          # 75 bpm, skin contact OK, one RR of 797/1024 s (Polar H7)
    "uint16": bytes([0x01, 90, 0x00]),                  # 90 bpm as a 16-bit value
    "energy_rr": bytes([0x18, 80, 0x10, 0x00, 0x00, 0x04, 0x00, 0x03]),   # 80 bpm, 16 kJ, RR 1.0 s and 0.75 s
    "no_contact": bytes([0x04, 0]),                     # strap supports contact detection, not touching skin
    "short": bytes([0x01, 90]),                         # broken: 16-bit flag but only one byte
}


def device(name="HeartCast", address="AA:BB:CC:DD:EE:FF", heart_rate=True):
    """A (BLEDevice-like, AdvertisementData-like) pair."""
    dev = SimpleNamespace(name=name, address=address)
    adv = SimpleNamespace(local_name=name, service_uuids=[HR_SERVICE] if heart_rate else [])
    return dev, adv


class FakeBleScanner:
    """find_device_by_filter() over a fixed list of nearby devices. Empty list = nothing around."""

    def __init__(self, nearby):
        self.nearby = list(nearby)
        self.scans = 0

    async def find_device_by_filter(self, filterfunc, timeout=10.0):
        self.scans += 1
        await asyncio.sleep(0)
        for dev, adv in self.nearby:
            if filterfunc(dev, adv):
                return dev
        return None


def fake_client(packets, every_s=0.01, drop_after=None, then_silent=False):
    """
    A BleakClient-like class. Sends `packets` every `every_s` seconds, then either
    disconnects (drop_after = number of packets, default: after the last) or, with
    then_silent=True, stays connected but sends nothing more.
    """

    class FakeBleClient:
        instances = []

        def __init__(self, dev, disconnected_callback=None):
            self.dev, self.on_drop = dev, disconnected_callback
            self.task = None
            FakeBleClient.instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            if self.task:
                self.task.cancel()

        async def start_notify(self, char, callback):
            async def play():
                for n, p in enumerate(packets):
                    await asyncio.sleep(every_s)
                    callback(char, bytearray(p))
                    if drop_after is not None and n + 1 >= drop_after:
                        break
                if not then_silent and self.on_drop:
                    self.on_drop(self)
            self.task = asyncio.create_task(play())

    return FakeBleClient
