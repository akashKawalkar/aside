# gcal/hooks.py — plugs the sync into the nightly job's calendar_pull / calendar_push steps (cloud/nightly.py Hooks).
from __future__ import annotations

from datetime import datetime

from cloud.nightly import Hooks
from gcal.model import CalendarClient, TasksClient
from gcal.sync import default_window, pull_events, push_events, sync_tasks


def make_hooks(pool, calendar: CalendarClient, tasks: TasksClient | None = None) -> Hooks:
    async def pull(now: datetime) -> dict:
        start, end = default_window(now)
        out = {"events": (await pull_events(pool, calendar, start=start, end=end, now=now)).as_detail()}
        if tasks is not None:
            out["tasks"] = (await sync_tasks(pool, tasks, now=now)).as_detail()
        return out

    async def push(now: datetime) -> dict:
        start, end = default_window(now)
        return (await push_events(pool, calendar, start=start, end=end, now=now)).as_detail()

    return Hooks(pull=pull, push=push)
