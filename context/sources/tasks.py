# context/sources/tasks.py — pending tasks, soonest due first.
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from context.items import Item, Situation

IST = ZoneInfo("Asia/Kolkata")
STALE_AFTER = timedelta(days=2)       # pending tasks overdue by more than this are left out of the prompt
FetchTasks = Callable[..., Awaitable[list[dict[str, Any]]]]   # storage.list_tasks bound to a pool


class TasksSource:
    name = "tasks"

    def __init__(self, fetch_tasks: FetchTasks) -> None:
        self._fetch = fetch_tasks

    async def fetch(self, situation: Situation) -> list[Item]:
        rows = await self._fetch(status="pending")
        cutoff = datetime.now(timezone.utc) - STALE_AFTER      # long-overdue tasks are noise in a prompt (plan M4.10)
        valid_rows = [r for r in rows if r["due_at"] is None or r["due_at"] >= cutoff]
        valid_rows.sort(key=lambda r: (r["due_at"] is None, r["due_at"]))
        items = []
        for n, row in enumerate(valid_rows):
            due = f" (due {row['due_at'].astimezone(IST):%a %d %b %H:%M})" if row["due_at"] else ""
            items.append(Item(id=f"task:{row['id']}", text=row["text"] + due, source=self.name,
                              provenance=f"task:{row['id']}", priority=-n))
        return items
