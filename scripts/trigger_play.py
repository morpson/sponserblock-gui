#!/usr/bin/env python3
"""Trigger playback of a video on the configured device.
Usage: python scripts/trigger_play.py D7B0QualKrA
"""
import sys
import asyncio
import traceback
from aiohttp import ClientSession
from iSponsorBlockTV.helpers import Config
from iSponsorBlockTV.ytlounge import YtLoungeApi

async def main(vid_id):
    try:
        cfg = Config('data')
        device = cfg.devices[0]
        screen_id = device['screen_id'] if isinstance(device, dict) else device.screen_id
        async with ClientSession() as s:
            lounge = YtLoungeApi(screen_id, cfg)
            await lounge.change_web_session(s)
            print('Refreshing auth...')
            try:
                await lounge.refresh_auth()
                print('refresh_auth ok')
            except Exception as e:
                print('refresh_auth failed:', e)
            print('Attempting connect...')
            try:
                ok = await lounge.connect()
                print('connect ->', ok)
            except Exception as e:
                print('connect failed:', e)
            print('Attempting play_video...', vid_id)
            try:
                ok = await lounge.play_video(vid_id)
                print('play_video ->', ok)
            except Exception as e:
                print('play_video failed:')
                traceback.print_exc()
    except Exception:
        traceback.print_exc()

if __name__ == '__main__':
    vid = sys.argv[1] if len(sys.argv) > 1 else 'D7B0QualKrA'
    asyncio.run(main(vid))
