"""
Watch the band's skin temperature live over Bluetooth (no cable). Used for calibrating the diode.

    .venv/bin/python tools/ble_watch.py

Prints the temperature and the raw sensor number (mV) every 5 seconds. Press Ctrl+C to stop.
"""

import asyncio

from bleak import BleakClient, BleakScanner

NAME = "Redline Band"
TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"   # band -> laptop (notify)


async def main():
    device = await BleakScanner.find_device_by_name(NAME, timeout=10.0)
    if device is None:
        print(f'"{NAME}" not found. Is it powered on and close by?')
        return
    buf = bytearray()
    latest = {}

    def on_data(_, data: bytearray):
        buf.extend(data)
        while b"\n" in buf:
            line, _, rest = buf.partition(b"\n")
            buf[:] = rest
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("TEMP:"):
                latest["temp"] = text[5:]
            elif text.startswith("#DIODE_MV:"):
                latest["mv"] = text[10:]
                print(f"skin temp {latest.get('temp', '?')} °C   |   raw sensor {latest['mv']} mV", flush=True)

    async with BleakClient(device) as client:
        print("Connected. Readings every 5 s (Ctrl+C to stop):")
        await client.start_notify(TX, on_data)
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
