# gcal/laptop.py — the laptop's share of the Google sync (cloud plan 5.2.15): sync when the server starts, every few minutes,
# and soon after anything that changes the schedule or tasks (the server sets `poke`). Does nothing without Google credentials.
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from gcal import google_client
from gcal.sync import sync_all

log = logging.getLogger("gcal.laptop")
IST = ZoneInfo("Asia/Kolkata")
DEBOUNCE = 3.0       # seconds: a burst of edits becomes one sync


async def run_laptop_sync(pool, interval: float, poke: asyncio.Event, *, calendar=None, tasks=None) -> None:
    """`calendar` / `tasks` substitute clients (tests pass fakes). Errors are logged and the loop goes on."""
    while True:
        try:
            if calendar is not None or google_client.configured():
                if calendar is None:
                    calendar, tasks = await asyncio.to_thread(lambda: (google_client.GoogleCalendar(), google_client.GoogleTasks()))
                out = await sync_all(pool, calendar, tasks, now=datetime.now(IST))
                if any(out.get(k) for k in out if k != "skipped"):
                    log.info("google sync: %s", out)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("google sync failed")
        try:
            await asyncio.wait_for(poke.wait(), timeout=interval)
            await asyncio.sleep(DEBOUNCE)
        except asyncio.TimeoutError:
            pass
        poke.clear()
