# gcal/google_client.py — the real Google Calendar and Google Tasks clients (the protocols in gcal/model.py).
# The Google libraries are blocking, so every call runs in a worker thread. Credentials are an OAuth refresh token (made once
# by scripts/google_auth.py) plus the client id/secret, all from the environment: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
# GOOGLE_REFRESH_TOKEN, and optionally GOOGLE_CALENDAR_ID (default "primary"; point it at a throwaway calendar to try things).
# The mapping functions are pure and unit-tested; the HTTP itself is only exercised against the live account.
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from gcal.model import CalEvent, CalTask

IST = ZoneInfo("Asia/Kolkata")
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/tasks"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


# ---------- pure mapping ----------

def _stamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def event_from_api(item: dict[str, Any]) -> CalEvent | None:
    """None for events this app does not mirror: all-day events (no clock time)."""
    cancelled = item.get("status") == "cancelled"
    start, end = (item.get(k) or {} for k in ("start", "end"))
    if not cancelled and not (start.get("dateTime") and end.get("dateTime")):
        return None
    private = (item.get("extendedProperties") or {}).get("private") or {}

    def number(key: str) -> int | None:
        value = private.get(key)
        return int(value) if value and str(value).isdigit() else None

    # A cancelled event may carry no times at all; the sync only needs its id and the stamp.
    placeholder = datetime.fromtimestamp(0, IST)
    return CalEvent(
        id=item["id"], title=item.get("summary") or "(untitled)",
        start_at=_stamp(start.get("dateTime")) or placeholder, end_at=_stamp(end.get("dateTime")) or placeholder,
        updated=_stamp(item.get("updated")), cancelled=cancelled, aside_id=number("aside_id"), task_id=number("task_id"),
    )


def event_to_api(event: CalEvent) -> dict[str, Any]:
    private = {k: str(v) for k, v in (("aside_id", event.aside_id), ("task_id", event.task_id)) if v is not None}
    return {
        "summary": event.title,
        "start": {"dateTime": event.start_at.astimezone(IST).isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": event.end_at.astimezone(IST).isoformat(), "timeZone": "Asia/Kolkata"},
        "extendedProperties": {"private": private},
    }


def task_from_api(item: dict[str, Any]) -> CalTask:
    due = _stamp(item.get("due"))
    return CalTask(
        id=item["id"], title=item.get("title") or "(untitled)", due=due.date() if due else None,
        done=item.get("status") == "completed", deleted=bool(item.get("deleted")), updated=_stamp(item.get("updated")),
    )


def task_to_api(task: CalTask) -> dict[str, Any]:
    body: dict[str, Any] = {"title": task.title, "status": "completed" if task.done else "needsAction"}
    body["due"] = f"{task.due.isoformat()}T00:00:00.000Z" if isinstance(task.due, date) else None
    if not task.done:
        body["completed"] = None          # un-ticking must clear the completion time
    return body


# ---------- credentials / services ----------

def credentials_from_env():
    from google.oauth2.credentials import Credentials

    try:
        return Credentials(
            None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"].strip(), token_uri=TOKEN_URI,
            client_id=os.environ["GOOGLE_CLIENT_ID"].strip(), client_secret=os.environ["GOOGLE_CLIENT_SECRET"].strip(), scopes=SCOPES,
        )
    except KeyError as exc:
        raise RuntimeError(f"{exc.args[0]} is not set (run scripts/google_auth.py once)") from None


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"))


def _build(api: str, version: str, credentials):
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials, cache_discovery=False)


def _is_gone(exc: Exception) -> bool:
    from googleapiclient.errors import HttpError

    return isinstance(exc, HttpError) and exc.resp.status in (404, 410)


class GoogleCalendar:
    def __init__(self, credentials=None, calendar_id: str | None = None, service=None) -> None:
        self.calendar_id = calendar_id or os.environ.get("GOOGLE_CALENDAR_ID", "").strip() or "primary"
        self._service = service or _build("calendar", "v3", credentials or credentials_from_env())

    def _list(self, start: datetime, end: datetime) -> list[CalEvent]:
        events, token = [], None
        while True:
            page = self._service.events().list(
                calendarId=self.calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(), showDeleted=True,
                singleEvents=True, maxResults=2500, pageToken=token,
            ).execute()
            events += [e for e in map(event_from_api, page.get("items", [])) if e is not None]
            token = page.get("nextPageToken")
            if not token:
                return events

    async def list_events(self, start: datetime, end: datetime) -> list[CalEvent]:
        return await asyncio.to_thread(self._list, start, end)

    async def insert_event(self, event: CalEvent) -> CalEvent:
        item = await asyncio.to_thread(lambda: self._service.events().insert(calendarId=self.calendar_id, body=event_to_api(event)).execute())
        return event_from_api(item)

    async def update_event(self, event: CalEvent) -> CalEvent:
        item = await asyncio.to_thread(
            lambda: self._service.events().patch(calendarId=self.calendar_id, eventId=event.id, body=event_to_api(event)).execute())
        return event_from_api(item)

    async def delete_event(self, event_id: str) -> None:
        def run():
            try:
                self._service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
            except Exception as exc:
                if not _is_gone(exc):
                    raise
        await asyncio.to_thread(run)


class GoogleTasks:
    LIST = "@default"

    def __init__(self, credentials=None, service=None) -> None:
        self._service = service or _build("tasks", "v1", credentials or credentials_from_env())

    def _list(self) -> list[CalTask]:
        tasks, token = [], None
        while True:
            page = self._service.tasks().list(
                tasklist=self.LIST, showCompleted=True, showHidden=True, showDeleted=True, maxResults=100, pageToken=token,
            ).execute()
            tasks += [task_from_api(t) for t in page.get("items", [])]
            token = page.get("nextPageToken")
            if not token:
                return tasks

    async def list_tasks(self) -> list[CalTask]:
        return await asyncio.to_thread(self._list)

    async def insert_task(self, task: CalTask) -> CalTask:
        item = await asyncio.to_thread(lambda: self._service.tasks().insert(tasklist=self.LIST, body=task_to_api(task)).execute())
        return task_from_api(item)

    async def update_task(self, task: CalTask) -> CalTask:
        item = await asyncio.to_thread(lambda: self._service.tasks().patch(tasklist=self.LIST, task=task.id, body=task_to_api(task)).execute())
        return task_from_api(item)

    async def delete_task(self, task_id: str) -> None:
        def run():
            try:
                self._service.tasks().delete(tasklist=self.LIST, task=task_id).execute()
            except Exception as exc:
                if not _is_gone(exc):
                    raise
        await asyncio.to_thread(run)
