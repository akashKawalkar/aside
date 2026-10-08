# gcal/hooks.py — plugs the sync into the nightly job's calendar_pull / calendar_push steps (cloud/nightly.py Hooks).
from __future__ import annotations

from datetime import datetime

from cloud.nightly import Hooks
from gcal.lock import sync_lock
from gcal.model import CalendarClient, TasksClient
from gcal.sync import default_window, forget_links_if_calendar_changed, pull_events, push_events, sync_tasks


def make_hooks(pool, calendar: CalendarClient, tasks: TasksClient | None = None) -> Hooks:
    # The laptop may be syncing the same account right now: wait for it (up to 90 s) rather than skip the night's sync.
    async def pull(now: datetime) -> dict:
        start, end = default_window(now)
        async with sync_lock(pool, wait=90) as got:
            if not got:
                return {"skipped": "another sync was still running"}
            await forget_links_if_calendar_changed(pool, calendar)
            out = {"events": (await pull_events(pool, calendar, start=start, end=end, now=now)).as_detail()}
            if tasks is not None:
                out["tasks"] = (await sync_tasks(pool, tasks, now=now)).as_detail()
            return out

    async def push(now: datetime) -> dict:
        start, end = default_window(now)
        async with sync_lock(pool, wait=90) as got:
            if not got:
                return {"skipped": "another sync was still running"}
            return (await push_events(pool, calendar, start=start, end=end, now=now)).as_detail()

    return Hooks(pull=pull, push=push)
