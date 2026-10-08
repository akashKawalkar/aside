"""Twice-daily, code-only observation refresh."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config import Patterns
from patterns.engine import run_patterns_once

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
RUN_AT = ((13, 0), (21, 0))
GRACE = timedelta(hours=2)      # a pass is still run if the poll lands this late; later than that the cycle is skipped


def due_slot(now: datetime, completed: set[tuple[str, int, int]]) -> tuple[str, int, int] | None:
    """The slot to run now, if any. Checked as a window, not an exact minute, so a poll that drifts past :00 cannot
    skip the pass. A laptop that was off for the whole window simply misses that cycle (plan §3.5)."""
    for hour, minute in RUN_AT:
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        slot = (now.date().isoformat(), hour, minute)
        if start <= now < start + GRACE and slot not in completed:
            return slot
    return None


async def run_pattern_worker(pool, cfg: Patterns, *, interval: float = 60.0) -> None:
    """Runs at 13:00 and 21:00 IST while the service is up; no catch-up call. Code-only: no model is ever called."""
    completed: set[tuple[str, int, int]] = set()
    while True:
        try:
            now = datetime.now(IST)
            slot = due_slot(now, completed)
            if slot:
                completed.add(slot)
                count = await run_patterns_once(pool, cfg, now=now)
                log.info("pattern pass at %s IST wrote %d observations", now.isoformat(), count)
            completed = {c for c in completed if c[0] == now.date().isoformat()}
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pattern worker failed")
        await asyncio.sleep(interval)
