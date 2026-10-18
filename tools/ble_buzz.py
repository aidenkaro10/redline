"""
Send one alert to the band over Bluetooth, hold it, then turn it off.

    .venv/bin/python tools/ble_buzz.py                 # flashing red + beeps for 10 s
    .venv/bin/python tools/ble_buzz.py LIGHTNING 8     # any pattern, any number of seconds

Patterns: HEAT_YELLOW, HEAT_RED, LIGHTNING, FLOOD_FIRE, MANDOWN, OFF
"""

import asyncio
import sys

from bleak import BleakClient, BleakScanner

NAME = "Redline Band"
RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"   # laptop -> band (write)


async def main(pattern: str, seconds: float):
    device = await BleakScanner.find_device_by_name(NAME, timeout=10.0)
    if device is None:
        print(f'"{NAME}" not found. Is it powered on and close by?')
        return
    async with BleakClient(device) as client:
        print(f"Connected. Band should show {pattern} now, for {seconds:.0f} s...")
        await client.write_gatt_char(RX, f"BUZZ:{pattern}\n".encode(), response=False)
        await asyncio.sleep(seconds)
        await client.write_gatt_char(RX, b"BUZZ:OFF\n", response=False)
        await asyncio.sleep(0.5)
    print("Back to green.")


if __name__ == "__main__":
    pattern = (sys.argv[1] if len(sys.argv) > 1 else "HEAT_RED").upper()
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    asyncio.run(main(pattern, seconds))
