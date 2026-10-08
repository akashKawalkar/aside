# gcal/fake.py — in-memory Google Calendar and Google Tasks, for tests (and a dry run before real credentials exist).
# `user_*` methods play the person editing on the Google side; the rest is the client interface (gcal/model.py).
from __future__ import annotations

import copy
import itertools
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from gcal.model import CalEvent, CalTask


class _Clock:
    """A clock the test can move. Every write by either side is stamped with it, like Google's `updated`."""

    def __init__(self, start: datetime | None = None) -> None:
        self.t = start or datetime(2031, 3, 3, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


class FakeCalendar:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or _Clock()
        self.events: dict[str, CalEvent] = {}
        self._ids = itertools.count(1)
        self.calls: list[str] = []

    async def list_events(self, start: datetime, end: datetime) -> list[CalEvent]:
        self.calls.append("list")
        return [copy.copy(e) for e in self.events.values() if e.start_at < end and e.end_at > start]

    async def insert_event(self, event: CalEvent) -> CalEvent:
        self.calls.append("insert")
        stored = copy.copy(event)
        stored.id, stored.updated, stored.cancelled = f"ev{next(self._ids)}", self.clock(), False
        self.events[stored.id] = stored
        return copy.copy(stored)

    async def update_event(self, event: CalEvent) -> CalEvent:
        self.calls.append("update")
        stored = copy.copy(event)
        stored.updated = self.clock()
        self.events[event.id] = stored
        return copy.copy(stored)

    async def delete_event(self, event_id: str) -> None:
        self.calls.append("delete")
        if event_id in self.events:
            self.events[event_id].cancelled, self.events[event_id].updated = True, self.clock()

    # ---- the person, in Google Calendar
    def user_add(self, title: str, start_at: datetime, end_at: datetime) -> str:
        return self.events_add(CalEvent(None, title, start_at, end_at))

    def events_add(self, event: CalEvent) -> str:
        event.id, event.updated = f"ev{next(self._ids)}", self.clock()
        self.events[event.id] = event
        return event.id

    def user_edit(self, event_id: str, **fields) -> None:
        event = self.events[event_id]
        for key, value in fields.items():
            setattr(event, key, value)
        event.updated = self.clock()

    def user_delete(self, event_id: str) -> None:
        self.events[event_id].cancelled, self.events[event_id].updated = True, self.clock()


class FakeTasks:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or _Clock()
        self.tasks: dict[str, CalTask] = {}
        self._ids = itertools.count(1)

    async def list_tasks(self) -> list[CalTask]:
        return [copy.copy(t) for t in self.tasks.values()]

    async def insert_task(self, task: CalTask) -> CalTask:
        stored = copy.copy(task)
        stored.id, stored.updated = f"gt{next(self._ids)}", self.clock()
        self.tasks[stored.id] = stored
        return copy.copy(stored)

    async def update_task(self, task: CalTask) -> CalTask:
        stored = copy.copy(task)
        stored.updated = self.clock()
        self.tasks[task.id] = stored
        return copy.copy(stored)

    async def delete_task(self, task_id: str) -> None:
        if task_id in self.tasks:
            self.tasks[task_id].deleted, self.tasks[task_id].updated = True, self.clock()

    # ---- the person, in Google Tasks / on the phone
    def user_add(self, title: str, due: date | None = None) -> str:
        task = CalTask(f"gt{next(self._ids)}", title, due, updated=self.clock())
        self.tasks[task.id] = task
        return task.id

    def user_edit(self, task_id: str, **fields) -> None:
        task = self.tasks[task_id]
        for key, value in fields.items():
            setattr(task, key, value)
        task.updated = self.clock()
