#!/usr/bin/env python3
"""Pair a YouTube TV device using a pairing code and save it to ./data/config.json

Usage: ./pair_and_add.py 829747015579
"""
import sys
import asyncio

import aiohttp

from iSponsorBlockTV.helpers import Config
from iSponsorBlockTV.ytlounge import YtLoungeApi


async def pair_and_add(code: str, data_dir: str = "data"):
    cfg = Config(data_dir)
    web = aiohttp.ClientSession()
    lounge = YtLoungeApi("iSponsorBlockTV")
    await lounge.change_web_session(web)
    try:
        pairing_code = int(code.replace("-", "").replace(" ", ""))
    except ValueError:
        print("Invalid pairing code format")
        await web.close()
        return 1

    print("Attempting to pair...")
    try:
        paired = await lounge.pair(pairing_code)
    except Exception as e:
        print("Pairing failed:", e)
        await web.close()
        return 1

    if not paired:
        print("Pairing unsuccessful.")
        await web.close()
        return 1

    device = {
        "screen_id": lounge.auth.screen_id,
        "name": lounge.screen_name,
        "offset": 0,
    }

    # Load or create config and append device
    cfg.devices = getattr(cfg, "devices", [])
    cfg.devices.append(device)
    cfg.save()
    print("Device added and saved to", f"{data_dir}/config.json")
    await web.close()
    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: pair_and_add.py <pairing_code>")
        sys.exit(2)
    code = sys.argv[1]
    rc = asyncio.run(pair_and_add(code))
    sys.exit(rc)


if __name__ == "__main__":
    main()
