# gcal/model.py — what the sync talks about, independent of Google's wire format. Times are timezone-aware.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass
class CalEvent:
    """One calendar event. `aside_id` / `task_id` travel in the event's private extendedProperties, so the link to our own
    rows survives edits made in Google Calendar. A cancelled event is a deletion (Google keeps it listed for a while)."""
    id: str | None
    title: str
    start_at: datetime
    end_at: datetime
    updated: datetime | None = None
    cancelled: bool = False
    aside_id: int | None = None
    task_id: int | None = None


@dataclass
class CalTask:
    """One Google Task. Google stores only a due DATE, not a time."""
    id: str | None
    title: str
    due: date | None = None
    done: bool = False
    deleted: bool = False
    updated: datetime | None = None


class CalendarClient(Protocol):
    async def list_events(self, start: datetime, end: datetime) -> list[CalEvent]:
        """Events overlapping the window, cancelled ones included."""

    async def insert_event(self, event: CalEvent) -> CalEvent:
        """Returns the stored event: its new id and `updated`."""

    async def update_event(self, event: CalEvent) -> CalEvent: ...

    async def delete_event(self, event_id: str) -> None:
        """Idempotent: deleting an event that is already gone is not an error."""


class TasksClient(Protocol):
    async def list_tasks(self) -> list[CalTask]:
        """Every task including completed and deleted ones."""

    async def insert_task(self, task: CalTask) -> CalTask: ...

    async def update_task(self, task: CalTask) -> CalTask: ...

    async def delete_task(self, task_id: str) -> None:
        """Idempotent."""
