# gcal/lock.py — only one sync at a time across every machine that shares the database (the laptop and the nightly Action
# both sync the same Google account). A Postgres advisory lock, so there is nothing to clean up if a process dies: the lock
# goes with its connection.
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

LOCK_KEY = 74_650_001      # arbitrary, only has to be the same everywhere


@asynccontextmanager
async def sync_lock(pool, wait: float = 0.0):
    """Yields True when this caller holds the lock, False when another sync is running (after waiting up to `wait` seconds)."""
    async with pool.connection() as conn:
        deadline = time.monotonic() + wait
        got = False
        while True:
            row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))).fetchone()
            got = bool(row[0])
            if got or time.monotonic() >= deadline:
                break
            await asyncio.sleep(2)
        try:
            yield got
        finally:
            if got:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
