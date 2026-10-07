from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from storage import (
    create_schedule_entry,
    update_schedule_entry,
    delete_schedule_entry,
)


MAX_TITLE_LENGTH = 200
DEFAULT_DURATION = timedelta(minutes=30)


def _validate_title(title: str) -> str:
    if not isinstance(title, str):
        raise ValueError("title must be a string")

    title = title.strip()

    if not title:
        raise ValueError("title must not be empty")

    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"title must be at most {MAX_TITLE_LENGTH} characters"
        )

    return title


def _validate_times(
    start_at: datetime,
    end_at: datetime,
) -> None:
    if not isinstance(start_at, datetime):
        raise ValueError("start_at must be a datetime")

    if not isinstance(end_at, datetime):
        raise ValueError("end_at must be a datetime")

    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("schedule times must be timezone-aware")

    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")


def _next_half_hour(now: datetime) -> datetime:
    now = now.replace(second=0, microsecond=0)

    if now.minute < 30:
        return now.replace(minute=30)

    return (
        now
        + timedelta(hours=1)
    ).replace(minute=0)


async def capture_schedule(
    pool,
    *,
    title: str,
    now: datetime,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> dict[str, Any]:
    title = _validate_title(title)

    default_time_used = start_at is None

    if start_at is None:
        start_at = _next_half_hour(now)

    if end_at is None:
        end_at = start_at + DEFAULT_DURATION

    _validate_times(start_at, end_at)

    entry = await create_schedule_entry(
        pool,
        title=title,
        start_at=start_at,
        end_at=end_at,
    )

    entry["default_time_used"] = default_time_used

    return entry


async def edit_schedule(
    pool,
    schedule_id: int,
    *,
    title: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    if not isinstance(schedule_id, int):
        raise ValueError("schedule_id must be an integer")

    title = _validate_title(title)
    _validate_times(start_at, end_at)

    entry = await update_schedule_entry(
        pool,
        schedule_id,
        title=title,
        start_at=start_at,
        end_at=end_at,
    )

    if entry is None:
        raise ValueError("schedule entry not found")

    return entry


async def remove_schedule(
    pool,
    schedule_id: int,
) -> bool:
    if not isinstance(schedule_id, int):
        raise ValueError("schedule_id must be an integer")

    return await delete_schedule_entry(pool, schedule_id)