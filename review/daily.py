# review/daily.py — the nightly schedule snapshot and day record, written once the day is over.
# The laptop is not always on, so nothing depends on firing at one exact minute: the worker checks every minute
# what is owed (today's snapshot after 23:55, anything missed in the last few days) and writes it, flagged `late`
# when it was written after the fact.
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config import DataQuality
from storage import (
    days_with_day_record,
    days_with_snapshot,
    get_schedule_snapshot,
    list_schedule_range,
    save_day_record,
    save_schedule_snapshot,
)

IST = ZoneInfo("Asia/Kolkata")
RECORD_GRACE = timedelta(hours=2)   # a day record written later than this after it became due is `late`
log = logging.getLogger("review.daily")


def _at(text: str) -> time:
    hour, minute = map(int, text.split(":"))
    return time(hour, minute)


def snapshots_due(now: datetime, have: set[date], cfg: DataQuality) -> list[tuple[date, bool]]:
    """(day, late) for each schedule snapshot owed. Today's is on time; a past day's can only be taken from the
    schedule as it is now, so it is `late`."""
    today = now.date()
    due = [(today - timedelta(days=n), True) for n in range(cfg.catchup_days, 0, -1) if today - timedelta(days=n) not in have]
    if now.time() >= _at(cfg.snapshot_time) and today not in have:
        due.append((today, False))
    return due


def records_due(now: datetime, have: set[date], cfg: DataQuality) -> list[tuple[date, bool]]:
    """(day, late) for each day record owed. Day D is due at `day_record_after` on D+1, when the sessionizer has
    settled; it is built from stored sessions, so being late costs nothing but the flag."""
    today = now.date()
    due = []
    for n in range(cfg.catchup_days, 0, -1):
        day = today - timedelta(days=n)
        ready_at = datetime.combine(day + timedelta(days=1), _at(cfg.day_record_after), tzinfo=now.tzinfo)
        if day not in have and now >= ready_at:
            due.append((day, now >= ready_at + RECORD_GRACE))
    return due


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"], "title": row["title"], "start_at": row["start_at"].isoformat(), "end_at": row["end_at"].isoformat(),
        "origin": row.get("origin", "user"), "edited_by_user": row.get("edited_by_user", False),
    }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def run_daily_once(pool, generator, cfg: DataQuality, *, now: datetime | None = None) -> dict[str, list[date]]:
    """Write whatever is owed. Returns the days written, for logging and tests."""
    now = now or datetime.now(IST)
    first, today = now.date() - timedelta(days=cfg.catchup_days), now.date()
    written: dict[str, list[date]] = {"snapshots": [], "records": []}

    for day, late in snapshots_due(now, await days_with_snapshot(pool, first=first, last=today), cfg):
        start = datetime.combine(day, time.min, tzinfo=IST)
        rows = await list_schedule_range(pool, start=start, end=start + timedelta(days=1))
        if await save_schedule_snapshot(pool, day=day, entries=[_entry(r) for r in rows], late=late):
            written["snapshots"].append(day)

    for day, late in records_due(now, await days_with_day_record(pool, first=first, last=today), cfg):
        entries = await get_schedule_snapshot(pool, day=day)
        if entries is None:   # no snapshot (e.g. first run): the schedule as it stands is the best there is
            start = datetime.combine(day, time.min, tzinfo=IST)
            entries = [_entry(r) for r in await list_schedule_range(pool, start=start, end=start + timedelta(days=1))]
        data = {"review": _json_safe(await generator.day_data(day)), "schedule": entries, "facts": []}  # facts: post-API
        if await save_day_record(pool, day=day, data=data, late=late):
            written["records"].append(day)

    return written


async def run_daily_worker(pool, generator, cfg: DataQuality, interval: float = 60.0) -> None:
    while True:
        try:
            written = await run_daily_once(pool, generator, cfg)
            if written["snapshots"] or written["records"]:
                log.info("daily records written: %s", written)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("daily worker failed")
        await asyncio.sleep(interval)
