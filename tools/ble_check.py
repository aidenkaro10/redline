"""
Quick check that the band works over Bluetooth (no cable needed for data).

Run in Terminal (macOS asks for Bluetooth permission the first time: click Allow):
    .venv/bin/python tools/ble_check.py

It finds "Redline Band", listens for 10 seconds, counts the lines it gets,
then makes the band go yellow for 3 seconds and back to green.
"""

import asyncio
import collections
import time

from bleak import BleakClient, BleakScanner

NAME = "Redline Band"
TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"   # band -> laptop (notify)
RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"   # laptop -> band (write)


async def main():
    print(f'Looking for "{NAME}" (10 s)...')
    device = await BleakScanner.find_device_by_name(NAME, timeout=10.0)
    if device is None:
        print("Not found. Is the band powered on and within a few meters? Is Bluetooth on?")
        return
    print(f"Found it: {device.address}. Connecting...")

    counts = collections.Counter()
    buf = bytearray()

    def on_data(_, data: bytearray):
        buf.extend(data)
        while b"\n" in buf:                      # one notification can hold part of a line or several lines
            line, _, rest = buf.partition(b"\n")
            buf[:] = rest
            text = line.decode("utf-8", "replace").strip()
            if text:
                counts[text.split(":", 1)[0]] += 1

    async with BleakClient(device) as client:
        print(f"Connected (packet size {client.mtu_size} bytes). Listening for 10 s...")
        await client.start_notify(TX, on_data)
        await asyncio.sleep(10)
        print("Lines received in 10 s:", dict(counts))
        print("Expect about 500 ACC, 50 PPG or 10 LIGHT, 2 TEMP.")
        print("Sending yellow for 3 s (watch the band: yellow + one long beep)...")
        await client.write_gatt_char(RX, b"BUZZ:HEAT_YELLOW\n", response=False)
        await asyncio.sleep(3)
        await client.write_gatt_char(RX, b"BUZZ:OFF\n", response=False)
        await client.stop_notify(TX)
    print("Done. If the counts look right and the band went yellow, Bluetooth works.")


if __name__ == "__main__":
    asyncio.run(main())
