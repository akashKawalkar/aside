# scripts/ui_check/fixtures.py — the rows a UI check needs, and the cleanup that removes everything it created.
# Everything is `zz`-titled or dated 2031 or logged under the model name `fake-gen`, so cleanup touches only its own rows.
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import storage  # noqa: E402
from schedule_gen.model import IST  # noqa: E402

TARGET_DAY = date(2031, 3, 4)        # "tomorrow" on the fixed clock; a Tuesday
LAST_TUESDAY = date(2031, 2, 25)


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=IST)


async def _purge(pool, *, with_logs: bool) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM schedule_draft WHERE target_day = %s", (TARGET_DAY,))
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
            await cur.execute("DELETE FROM instruction_records WHERE text LIKE 'zz%'")
            if with_logs:                                       # rows written by the fake model's approvals
                await cur.execute("DELETE FROM llm_trace WHERE model = 'fake-gen'")
                await cur.execute("DELETE FROM compile_log WHERE model = 'fake-gen'")


async def seed() -> None:
    """A clean slate plus last Tuesday: a workout and a focus block the user made (the rule-based draft copies them)."""
    pool = storage.make_pool()
    await pool.open()
    try:
        await _purge(pool, with_logs=False)
        await storage.create_schedule_entry(pool, title="zz Gym", start_at=_at(LAST_TUESDAY, 7), end_at=_at(LAST_TUESDAY, 8))
        await storage.create_schedule_entry(pool, title="zz Deep work", start_at=_at(LAST_TUESDAY, 9), end_at=_at(LAST_TUESDAY, 12))
    finally:
        await pool.close()


async def clean() -> None:
    pool = storage.make_pool()
    await pool.open()
    try:
        await _purge(pool, with_logs=True)
    finally:
        await pool.close()
