from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from sessions.job import (
    SETTLE_LAG,
    _day_bounds,
    sessionize_days,
    sessionize_recent,
    sessionize_since,
)

DAY = date(2026, 10, 5)
MIDNIGHT = _day_bounds(DAY)[0]
NINE = MIDNIGHT + timedelta(hours=9)  # well away from day edges


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


def row(event_id: int, app, start_min: float, end_min: float, *, kind="app_focus"):
    return {
        "id": event_id,
        "source": "laptop",
        "kind": kind,
        "app": app,
        "window_title": None,
        "domain": None,
        "ts_start": NINE + minutes(start_min),
        "ts_end": NINE + minutes(end_min),
        "payload": {},
    }


class FakeStore:
    """In-memory stand-in for the storage functions, with the same contract."""

    def __init__(self, rows):
        self.rows = rows
        self.sessions: list[dict] = []
        self.first_ids: set[int] = set()
        self.settled_before_seen: list[datetime] = []

    async def fetch(self, pool, *, start, end, settled_before):
        self.settled_before_seen.append(settled_before)
        return [
            r for r in self.rows
            if start <= r["ts_start"] < end and r["ts_end"] <= settled_before
        ]

    async def save(self, pool, sessions):
        created = 0
        for s in sessions:
            first = s["event_ids"][0]
            if first in self.first_ids:
                continue
            self.first_ids.add(first)
            self.sessions.append(s)
            created += 1
        return created


# Code 6 min, Chrome 3 min, Code 5 min: Code reaches 10 minutes at event 3,
# Chrome stays pending (3 min) and never becomes a session.
CODE_THEN_CHROME = [
    row(1, "Code.exe", 0, 6),
    row(2, "chrome.exe", 6, 9),
    row(3, "Code.exe", 9, 14),
]

LATE = NINE + timedelta(days=2)


async def run(store, now=LATE, days=(DAY,)):
    return await sessionize_days(
        None, days, now=now, fetch=store.fetch, save=store.save
    )


async def test_creates_session_with_ordered_event_ids():
    store = FakeStore(CODE_THEN_CHROME)

    result = await run(store)

    assert (result.days, result.created) == (1, 1)
    [session] = store.sessions
    assert session["kind"] == "app"
    assert session["app"] == "code"  # ".exe" stripped, so it matches the allowlist
    assert session["event_ids"] == [1, 3]  # Chrome's pending fragment is not included
    assert session["active_seconds"] == pytest.approx(11 * 60)
    # Wall-clock is longer than active time: Chrome sat in between.
    assert (session["ended_at"] - session["started_at"]) == minutes(14)


async def test_rerun_is_idempotent():
    store = FakeStore(CODE_THEN_CHROME)

    first = await run(store)
    second = await run(store)

    assert first.created == 1
    assert second.created == 0
    assert len(store.sessions) == 1


async def test_only_settled_events_are_used():
    store = FakeStore(CODE_THEN_CHROME)
    last_end = NINE + minutes(14)

    # Only 5 minutes after the last event: inside the settle lag, so event 3
    # is held back and Code (6 min) has not become a session yet.
    early = await run(store, now=last_end + minutes(5))
    assert early.created == 0
    assert store.settled_before_seen[-1] == last_end + minutes(5) - SETTLE_LAG

    # Later, the same events settle and the session appears, identical to a
    # single late run.
    later = await run(store, now=last_end + SETTLE_LAG + minutes(1))
    assert later.created == 1
    assert store.sessions[0]["event_ids"] == [1, 3]


async def test_idle_event_becomes_its_own_session():
    store = FakeStore([row(10, None, 0, 5, kind="idle")])

    await run(store)

    [session] = store.sessions
    assert session["kind"] == "idle"
    assert session["app"] is None
    assert session["event_ids"] == [10]


async def test_short_events_make_no_session():
    store = FakeStore([row(1, "Code.exe", 0, 0.1)])  # 6 seconds

    result = await run(store)

    assert result.created == 0
    assert store.sessions == []


async def test_unreadable_rows_are_skipped():
    rows = CODE_THEN_CHROME + [row(99, "Code.exe", 20, 25, kind="weird")]
    store = FakeStore(rows)

    result = await run(store)

    assert result.created == 1  # the bad row is ignored, the rest still works


async def test_day_that_has_not_started_is_skipped():
    store = FakeStore([])
    now = NINE  # the day after DAY is entirely in the future

    result = await run(store, now=now, days=[DAY + timedelta(days=1)])

    assert result.days == 0
    assert store.settled_before_seen == []


async def test_recent_covers_yesterday_and_today():
    store = FakeStore([])

    result = await sessionize_recent(
        None, now=NINE + timedelta(days=2), fetch=store.fetch, save=store.save
    )

    assert result.days == 2


async def test_since_covers_every_day_through_today_and_rejects_the_future():
    store = FakeStore([])
    now = NINE + timedelta(days=3)  # 8 Oct

    result = await sessionize_since(
        None, DAY, now=now, fetch=store.fetch, save=store.save
    )
    assert result.days == 4  # 5, 6, 7, 8 Oct

    with pytest.raises(ValueError):
        await sessionize_since(None, date(2030, 1, 1), now=now, fetch=store.fetch, save=store.save)


async def test_browser_events_are_not_sessionized():
    # Same minutes as the Code session, reported again by the browser reader.
    browser_row = {**row(50, "chrome.exe", 0, 14), "source": "browser"}
    store = FakeStore(CODE_THEN_CHROME + [browser_row])

    result = await run(store)

    assert result.created == 1
    [session] = store.sessions
    assert 50 not in session["event_ids"]
    assert session["active_seconds"] == pytest.approx(11 * 60)  # not inflated
