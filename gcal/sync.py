# gcal/sync.py — two-way sync between the database and Google Calendar / Google Tasks (cloud plan sections 2 and 5.1.8).
#
# Calendar: schedule rows mirror events. A row remembers the event (gcal_event_id) and the `updated` stamp both sides agreed
# on (gcal_updated). Since then: the calendar changed if event.updated is newer; we changed if the row's updated_at is newer.
#   - only one side changed: that side wins (a pull applies the calendar's; a push sends ours).
#   - both changed: the later edit wins, and if the edits are within TIE of each other the calendar wins.
#   - an event deleted on the calendar soft-deletes the row ("the plan changed" is data); the linked task is NOT completed.
# Tasks: the database holds the truth (slips, urgency, dropped state). Google Tasks is a mirror so they can be ticked on the
# phone: a tick on either side completes the task on both; a task made in Google is imported; a dropped task is removed there.
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import storage
from gcal.model import CalEvent, CalendarClient, CalTask, TasksClient

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
TIE = timedelta(seconds=120)    # edits this close together count as simultaneous: the calendar wins
TOL = timedelta(seconds=1)      # clock jitter between our stamp and Google's


@dataclass
class SyncReport:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    pushed: int = 0
    completed: int = 0
    dropped: int = 0
    notes: list[str] = field(default_factory=list)

    def as_detail(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}


def default_window(now: datetime) -> tuple[datetime, datetime]:
    """Yesterday (to catch late edits) through two weeks ahead, by the IST calendar."""
    today = datetime.combine(now.astimezone(IST).date(), time.min, IST)
    return today - timedelta(days=1), today + timedelta(days=15)


def _same(row: dict, event: CalEvent) -> bool:
    return row["title"] == event.title and row["start_at"] == event.start_at and row["end_at"] == event.end_at


# ---------- which calendar the links belong to ----------

async def forget_links_if_calendar_changed(pool, calendar: CalendarClient) -> bool:
    """Event ids only mean something inside the calendar that issued them. When the sync is pointed at a different calendar
    (the test one, then the real one), every stored link is cleared so rows are pushed fresh instead of failing with
    "not found". Returns True when links were cleared."""
    current = getattr(calendar, "calendar_id", "default")
    state = await storage.get_review_state(pool, "gcal") or {}
    if state.get("calendar_id") == current:
        return False
    # Also when nothing was recorded yet: links may have been made against another calendar. Clearing is harmless, because the
    # pull re-adopts an existing event by the aside_id stored in it.
    log.info("Google calendar is now %s (was %s): clearing stored event links", current, state.get("calendar_id"))
    async with pool.connection() as conn:
        await conn.execute("UPDATE schedule SET gcal_event_id = NULL, gcal_updated = NULL")
    await storage.set_review_state(pool, "gcal", {"calendar_id": current})
    return True


# ---------- calendar ----------

async def pull_events(pool, client: CalendarClient, *, start: datetime, end: datetime, now: datetime) -> SyncReport:
    report = SyncReport()
    rows = await storage.list_sync_entries(pool, start=start, end=end)
    by_event = {r["gcal_event_id"]: r for r in rows if r["gcal_event_id"]}

    for ev in await client.list_events(start, end):
        row = by_event.get(ev.id)
        if row is None and ev.aside_id is not None:           # pushed by us earlier but the link was lost: adopt it
            candidate = await storage.get_entry(pool, ev.aside_id)
            if candidate is not None and candidate["gcal_event_id"] in (None, ev.id):
                row = candidate

        if ev.cancelled:
            if row is not None and row["deleted_at"] is None:
                await storage.soft_delete_from_calendar(pool, row["id"], at=now)
                report.deleted += 1
            continue

        if row is None:
            task_id = ev.task_id if ev.task_id is not None and await storage.get_task(pool, task_id=ev.task_id) else None
            await storage.insert_synced_entry(
                pool, title=ev.title, start_at=ev.start_at, end_at=ev.end_at, gcal_event_id=ev.id,
                gcal_updated=ev.updated or now, task_id=task_id,
            )
            report.created += 1
            continue

        if row["deleted_at"] is not None:
            continue                                           # we deleted it; the next push removes the event

        stamp = ev.updated or now
        if _same(row, ev):
            if row["gcal_event_id"] != ev.id or row["gcal_updated"] != stamp:
                await storage.touch_synced(pool, row["id"], gcal_event_id=ev.id, gcal_updated=stamp)
            continue

        synced = row["gcal_updated"]
        cal_changed = synced is None or stamp > synced + TOL
        local_changed = synced is None or row["updated_at"] > synced + TOL
        calendar_wins = cal_changed and (not local_changed or stamp >= row["updated_at"] - TIE)
        if calendar_wins:
            await storage.apply_calendar_change(pool, row["id"], title=ev.title, start_at=ev.start_at, end_at=ev.end_at,
                                                gcal_updated=stamp)
            report.updated += 1
        # else: our edit is newer (or the calendar did not change): push_events sends it

    return report


async def push_events(pool, client: CalendarClient, *, start: datetime, end: datetime, now: datetime) -> SyncReport:
    report = SyncReport()
    for row in await storage.list_sync_entries(pool, start=start, end=end):
        synced = row["gcal_updated"]

        if row["deleted_at"] is not None:
            # Deleted on our side after the last agreement: remove the event. (A calendar-side delete sets gcal_updated to
            # the deletion time, so it is not pushed back.)
            if row["gcal_event_id"] and synced is not None and synced < row["deleted_at"]:
                await client.delete_event(row["gcal_event_id"])
                await storage.touch_synced(pool, row["id"], gcal_event_id=row["gcal_event_id"], gcal_updated=now)
                report.deleted += 1
            continue

        event = CalEvent(row["gcal_event_id"], row["title"], row["start_at"], row["end_at"], aside_id=row["id"], task_id=row["task_id"])
        if row["gcal_event_id"] is None:
            stored = await client.insert_event(event)
        elif synced is None or row["updated_at"] > synced + TOL:
            stored = await client.update_event(event)
        else:
            continue
        await storage.touch_synced(pool, row["id"], gcal_event_id=stored.id, gcal_updated=stored.updated or now)
        report.pushed += 1

    return report


# ---------- tasks ----------

def _due_date(due_at: datetime | None) -> date | None:
    return due_at.astimezone(IST).date() if due_at else None


def _due_at(due: date | None) -> datetime | None:
    """Google keeps a date only: a task due on a date is due by the end of that day, IST."""
    return datetime.combine(due, time(23, 59), IST) if due else None


async def sync_tasks(pool, client: TasksClient, *, now: datetime) -> SyncReport:
    report = SyncReport()
    google = {g.id: g for g in await client.list_tasks()}
    rows = await storage.list_task_sync_rows(pool)
    by_gtask = {r["gtask_id"]: r for r in rows if r["gtask_id"]}

    # ---- pull
    for g in google.values():
        row = by_gtask.get(g.id)
        if row is None:
            if not g.done and not g.deleted:                   # made in Google Tasks (the phone): import it
                task_id = await storage.insert_task_from_google(pool, text=g.title, due_at=_due_at(g.due), gtask_id=g.id,
                                                                gtask_updated=g.updated or now)
                rows.append({"id": task_id, "text": g.title, "due_at": _due_at(g.due), "status": "pending", "gtask_id": g.id,
                             "gtask_updated": g.updated or now})
                report.created += 1
            continue
        if row["status"] != "pending":
            continue
        if g.done:
            await storage.complete_task(pool, task_id=row["id"])
            row["status"] = "completed"
            report.completed += 1
        elif g.deleted:
            await storage.drop_task(pool, row["id"], reason="deleted in Google Tasks", at=now)
            row["status"] = "dropped"
            report.dropped += 1
        else:
            newer = row["gtask_updated"] is None or (g.updated or now) > row["gtask_updated"] + TOL
            differs = g.title != row["text"] or g.due != _due_date(row["due_at"])
            if differs and newer:                              # edited on the Google side since we last agreed
                await storage.update_task(pool, task_id=row["id"], text=g.title, due_at=_due_at(g.due) or row["due_at"])
                await storage.set_task_gtask(pool, row["id"], gtask_id=g.id, gtask_updated=g.updated or now)
                row["text"], row["due_at"] = g.title, _due_at(g.due) or row["due_at"]
                report.updated += 1

    # ---- push (the database is the truth)
    for row in rows:
        if row["status"] == "pending":
            if row["gtask_id"] is None:
                stored = await client.insert_task(CalTask(None, row["text"], _due_date(row["due_at"])))
                await storage.set_task_gtask(pool, row["id"], gtask_id=stored.id, gtask_updated=stored.updated or now)
                report.pushed += 1
                continue
            g = google.get(row["gtask_id"])
            if g is not None and not g.deleted and not g.done and (g.title != row["text"] or g.due != _due_date(row["due_at"])):
                fresh = (await storage.get_task(pool, task_id=row["id"])) or row
                stored = await client.update_task(CalTask(g.id, fresh["text"], _due_date(fresh["due_at"])))
                await storage.set_task_gtask(pool, row["id"], gtask_id=g.id, gtask_updated=stored.updated or now)
                report.pushed += 1
        elif row["gtask_id"]:
            g = google.get(row["gtask_id"])
            if g is None or g.deleted:
                continue
            if row["status"] == "completed" and not g.done:
                await client.update_task(CalTask(g.id, g.title, g.due, done=True))
                report.pushed += 1
            elif row["status"] == "dropped":
                await client.delete_task(g.id)
                report.pushed += 1

    return report


async def sync_all(pool, calendar: CalendarClient, tasks: TasksClient | None, *, now: datetime) -> dict[str, dict]:
    """Pull then push, for events and tasks. The nightly job's `calendar_pull` / `calendar_push` steps call the halves below."""
    from gcal.lock import sync_lock

    start, end = default_window(now)
    async with sync_lock(pool) as got:
        if not got:
            return {"skipped": "another sync is running"}
        await forget_links_if_calendar_changed(pool, calendar)
        out = {"pull": (await pull_events(pool, calendar, start=start, end=end, now=now)).as_detail()}
        if tasks is not None:
            out["tasks"] = (await sync_tasks(pool, tasks, now=now)).as_detail()
        out["push"] = (await push_events(pool, calendar, start=start, end=end, now=now)).as_detail()
        return out
