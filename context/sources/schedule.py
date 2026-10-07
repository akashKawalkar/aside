# context/sources/schedule.py — today's and tomorrow's schedule entries, in IST.
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from context.items import Item, Situation

IST = ZoneInfo("Asia/Kolkata")
FetchRange = Callable[..., Awaitable[list[dict[str, Any]]]]   # storage.list_schedule_range bound to a pool


class ScheduleSource:
    name = "schedule"

    def __init__(self, fetch_range: FetchRange, *, days: int = 2, clock: Callable[[], datetime] | None = None) -> None:
        self._fetch = fetch_range
        self._days = days
        self._clock = clock or (lambda: datetime.now(IST))

    async def fetch(self, situation: Situation) -> list[Item]:
        start = datetime.combine(self._clock().astimezone(IST).date(), time.min, IST)
        rows = await self._fetch(start=start, end=start + timedelta(days=self._days))
        items = []
        for row in rows:
            s, e = row["start_at"].astimezone(IST), row["end_at"].astimezone(IST)
            when = f"{s:%a %d %b} {s:%H:%M}-{e:%H:%M}"
            items.append(Item(id=f"schedule:{row['id']}", text=f"{when} {row['title']}", source=self.name,
                              provenance=f"schedule:{row['id']}", valid_to=e,
                              priority=-int(s.timestamp() // 60)))   # earlier entries first
        return items
