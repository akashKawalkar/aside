# sessions/job.py
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import ValidationError

from sessions.sessionizer import Sessionizer
from storage import Event, fetch_events_range, save_sessions

log = logging.getLogger(__name__)

# Only app-focus events from the laptop probe become sessions. Browser events
# (source "browser") describe the same minutes in more detail, so counting them
# too would double the time.
SESSION_SOURCE = "laptop"

# Events must have ended at least this long ago before they are used. It
# covers spool rotation and ingest delay, so an event that is still on its
# way into the database cannot change a day that was already sessionized.
SETTLE_LAG = timedelta(minutes=15)

FetchEvents = Callable[..., Awaitable[list[dict[str, Any]]]]
SaveSessions = Callable[..., Awaitable[int]]


@dataclass(frozen=True)
class SessionizeResult:
    days: int
    created: int


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """Local midnight to local midnight, as timezone-aware datetimes."""
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start, end


def _to_event(row: dict[str, Any]) -> Event | None:
    try:
        return Event(
            source=row["source"],
            kind=row["kind"],
            app=row["app"],
            window_title=row["window_title"],
            domain=row["domain"],
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            payload=row["payload"] or {},
        )
    except ValidationError:
        log.warning("skipping unreadable event id=%s", row.get("id"))
        return None


async def sessionize_days(
    pool,
    days: Iterable[date],
    *,
    now: datetime | None = None,
    lag: timedelta = SETTLE_LAG,
    sessionizer: Sessionizer | None = None,
    fetch: FetchEvents = fetch_events_range,
    save: SaveSessions = save_sessions,
) -> SessionizeResult:
    """
    Sessionize whole local days, idempotently.

    Each day is recomputed from its local midnight with an empty state, so
    the result for a given set of events is always the same. Sessions that
    were stored by an earlier run are recognised by their first event and
    skipped; only new ones are written. Pending fragments that have not yet
    reached the session threshold are never stored, because they are not
    sessions yet and a later run will see them again.

    Pending state is not carried across midnight: activity that is still
    pending at the end of a day does not join the next day's sessions.
    """
    now = now or datetime.now().astimezone()
    sessionizer = sessionizer or Sessionizer()

    processed = 0
    created = 0

    for day in sorted(set(days)):
        start, end = _day_bounds(day)
        settled_before = min(end, now - lag)

        if settled_before <= start:
            continue  # nothing in this day is settled yet

        rows = await fetch(
            pool,
            start=start,
            end=end,
            settled_before=settled_before,
        )

        events: list[Event] = []
        event_ids: dict[int, int] = {}  # id(Event object) -> database id

        for row in rows:
            if row.get("source", SESSION_SOURCE) != SESSION_SOURCE:
                continue

            event = _to_event(row)

            if event is not None:
                events.append(event)
                event_ids[id(event)] = row["id"]

        candidates = sessionizer.sessionize(events)

        created += await save(
            pool,
            [
                {
                    "kind": candidate.kind,
                    "app": candidate.app,
                    "started_at": candidate.started_at,
                    "ended_at": candidate.ended_at,
                    "active_seconds": candidate.duration.total_seconds(),
                    "event_ids": [
                        event_ids[id(fragment.event)]
                        for fragment in candidate.fragments
                    ],
                }
                for candidate in candidates
            ],
        )
        processed += 1

    return SessionizeResult(days=processed, created=created)


async def sessionize_recent(
    pool,
    *,
    now: datetime | None = None,
    **kwargs: Any,
) -> SessionizeResult:
    """Yesterday and today: the days that can still receive events."""
    now = now or datetime.now().astimezone()
    today = now.astimezone().date()

    return await sessionize_days(
        pool,
        [today - timedelta(days=1), today],
        now=now,
        **kwargs,
    )


async def sessionize_since(
    pool,
    since: date,
    *,
    now: datetime | None = None,
    **kwargs: Any,
) -> SessionizeResult:
    """Every day from `since` through today, for backfilling history."""
    now = now or datetime.now().astimezone()
    today = now.astimezone().date()

    if since > today:
        raise ValueError("since must not be in the future")

    days = [since + timedelta(days=n) for n in range((today - since).days + 1)]

    return await sessionize_days(pool, days, now=now, **kwargs)
