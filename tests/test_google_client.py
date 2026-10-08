"""The pure mapping between Google's JSON and our CalEvent / CalTask, and the client's calls against a stubbed service object.
The live API is exercised only with real credentials."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from gcal.google_client import (
    GoogleCalendar, GoogleTasks, IST, configured, event_from_api, event_to_api, task_from_api, task_to_api,
)
from gcal.model import CalEvent, CalTask


def test_timed_event_round_trips_with_our_ids():
    ev = CalEvent(None, "Gym", datetime(2031, 3, 4, 7, 0, tzinfo=IST), datetime(2031, 3, 4, 8, 0, tzinfo=IST), aside_id=5, task_id=9)
    body = event_to_api(ev)
    assert body["start"] == {"dateTime": "2031-03-04T07:00:00+05:30", "timeZone": "Asia/Kolkata"}
    assert body["extendedProperties"]["private"] == {"aside_id": "5", "task_id": "9"}

    back = event_from_api({"id": "e1", "summary": "Gym", "updated": "2031-03-03T12:00:01.500Z",
                           **{k: body[k] for k in ("start", "end", "extendedProperties")}})
    assert (back.id, back.title, back.aside_id, back.task_id, back.cancelled) == ("e1", "Gym", 5, 9, False)
    assert back.start_at == ev.start_at and back.updated == datetime(2031, 3, 3, 12, 0, 1, 500000, tzinfo=timezone.utc)


def test_all_day_events_are_ignored_and_cancelled_ones_kept_without_times():
    assert event_from_api({"id": "a", "summary": "Holiday", "start": {"date": "2031-03-04"}, "end": {"date": "2031-03-05"}}) is None
    gone = event_from_api({"id": "b", "status": "cancelled", "updated": "2031-03-03T12:00:00Z"})
    assert gone.cancelled and gone.id == "b" and gone.updated is not None


def test_event_without_our_properties_has_no_ids():
    ev = event_from_api({"id": "c", "summary": "Dentist", "start": {"dateTime": "2031-03-04T15:00:00+05:30"},
                         "end": {"dateTime": "2031-03-04T16:00:00+05:30"}})
    assert ev.aside_id is None and ev.task_id is None


def test_task_mapping_uses_dates_and_status():
    t = task_from_api({"id": "t1", "title": "Milk", "due": "2031-03-04T00:00:00.000Z", "status": "completed",
                       "updated": "2031-03-03T12:00:00.000Z"})
    assert (t.due, t.done, t.deleted) == (date(2031, 3, 4), True, False)
    assert task_to_api(CalTask("t1", "Milk", date(2031, 3, 4))) == {
        "title": "Milk", "status": "needsAction", "due": "2031-03-04T00:00:00.000Z", "completed": None}
    assert task_to_api(CalTask("t1", "Milk", None, done=True)) == {"title": "Milk", "status": "completed", "due": None}
    assert task_from_api({"id": "t2", "deleted": True}).deleted is True


class Chain:
    """service.events().list(...).execute(): records the arguments, returns the scripted pages in order."""

    def __init__(self, pages):
        self.pages, self.log = list(pages), []

    def events(self):
        return self

    def list(self, **kw):
        self.log.append(kw)
        return self

    def execute(self):
        return self.pages.pop(0)


def test_calendar_pages_through_results_and_asks_for_deleted_events():
    page1 = {"items": [{"id": "e1", "summary": "A", "start": {"dateTime": "2031-03-04T07:00:00+05:30"},
                        "end": {"dateTime": "2031-03-04T08:00:00+05:30"}}], "nextPageToken": "n"}
    page2 = {"items": [{"id": "e2", "status": "cancelled"}]}
    svc = Chain([page1, page2])
    start = datetime(2031, 3, 3, tzinfo=IST)
    events = asyncio.run(GoogleCalendar(service=svc, calendar_id="primary").list_events(start, start + timedelta(days=2)))
    assert [e.id for e in events] == ["e1", "e2"] and events[1].cancelled
    assert svc.log[0]["showDeleted"] is True and svc.log[0]["singleEvents"] is True and svc.log[1]["pageToken"] == "n"


def test_deleting_something_already_gone_is_not_an_error():
    from googleapiclient.errors import HttpError

    class Gone:
        status, reason = 410, "Gone"

    class Boom:
        def execute(self):
            raise HttpError(Gone(), b"gone")

    class Svc:
        def events(self):
            return self

        def tasks(self):
            return self

        def delete(self, **kw):
            return Boom()

    asyncio.run(GoogleCalendar(service=Svc(), calendar_id="primary").delete_event("e1"))
    asyncio.run(GoogleTasks(service=Svc()).delete_task("t1"))


def test_configured_needs_all_three_values(monkeypatch):
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert configured() is False
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "a")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "b")
    assert configured() is False
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "c")
    assert configured() is True
