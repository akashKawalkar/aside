"""The laptop's Google sync: one sync at a time across machines (advisory lock), a loop that syncs at start-up and again soon after
an edit, and the server poking it after schedule/task changes. FakeCalendar/FakeTasks only; rows are `zz`-titled, dated 2031."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

import gcal.laptop as laptop
import storage
from capture.server import create_app
from gcal.fake import FakeCalendar, FakeTasks
from gcal.lock import sync_lock
from gcal.sync import IST, sync_all

NOW = datetime(2031, 3, 3, 12, 0, tzinfo=timezone.utc)
DAY = date(2031, 3, 4)


def at(h, day=DAY):
    return datetime(day.year, day.month, day.day, h, 0, tzinfo=IST)


async def _clean(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
            await cur.execute("DELETE FROM task_log WHERE snapshot->>'text' LIKE 'zz%'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    await _clean(p)
    yield p
    await _clean(p)
    await p.close()


async def test_only_one_sync_holds_the_lock_and_it_is_released_afterwards(pool):
    async with sync_lock(pool) as first:
        assert first is True
        async with sync_lock(pool) as second:
            assert second is False                                  # someone else is syncing: skip, do not wait
    async with sync_lock(pool) as again:
        assert again is True


async def test_a_sync_that_finds_the_lock_taken_does_nothing(pool):
    await storage.create_schedule_entry(pool, title="zz locked out", start_at=at(9), end_at=at(10))
    cal = FakeCalendar()
    async with sync_lock(pool):
        assert await sync_all(pool, cal, None, now=NOW) == {"skipped": "another sync is running"}
    assert cal.events == {}
    assert (await sync_all(pool, cal, None, now=NOW))["push"] == {"pushed": 1}


async def test_the_loop_syncs_at_start_up_and_again_after_a_poke(pool, monkeypatch):
    monkeypatch.setattr(laptop, "DEBOUNCE", 0.05)
    await storage.create_schedule_entry(pool, title="zz first", start_at=at(9), end_at=at(10))
    cal, tasks, poke = FakeCalendar(), FakeTasks(), asyncio.Event()
    # sync_all uses the real clock when called from the loop, so put the rows inside a window around now
    now = datetime.now(IST)
    await storage.create_schedule_entry(pool, title="zz today", start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=2))

    worker = asyncio.create_task(laptop.run_laptop_sync(pool, 3600, poke, calendar=cal, tasks=tasks))
    try:
        for _ in range(100):
            if any(e.title == "zz today" for e in cal.events.values()):
                break
            await asyncio.sleep(0.05)
        assert any(e.title == "zz today" for e in cal.events.values())             # synced on start-up, with no poke

        await storage.create_schedule_entry(pool, title="zz later", start_at=now + timedelta(hours=3), end_at=now + timedelta(hours=4))
        assert not any(e.title == "zz later" for e in cal.events.values())          # the interval is an hour: nothing yet
        poke.set()
        for _ in range(100):
            if any(e.title == "zz later" for e in cal.events.values()):
                break
            await asyncio.sleep(0.05)
        assert any(e.title == "zz later" for e in cal.events.values())             # the poke brought the next sync forward
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


async def test_the_server_pokes_the_sync_after_changes_but_not_after_reads_or_failures(pool):
    app = create_app()
    app.state.db_pool = pool
    app.state.sync_poke = asyncio.Event()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/tasks")
        assert not app.state.sync_poke.is_set()                                    # reading changes nothing
        await client.post("/tasks", json={})                                       # rejected (422): nothing changed
        assert not app.state.sync_poke.is_set()
        ok = await client.post("/tasks", json={"text": "zz poke me"})
        assert ok.status_code < 400 and app.state.sync_poke.is_set()


async def test_pointing_the_sync_at_another_calendar_clears_the_links_and_republishes(pool):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM review_state WHERE key = 'gcal'")
    await storage.create_schedule_entry(pool, title="zz moves calendars", start_at=at(9), end_at=at(10))
    test_cal, real_cal = FakeCalendar(), FakeCalendar()
    test_cal.calendar_id, real_cal.calendar_id = "test@group", "primary"

    assert (await sync_all(pool, test_cal, None, now=NOW))["push"] == {"pushed": 1}
    assert (await sync_all(pool, test_cal, None, now=NOW)) == {"pull": {}, "push": {}}      # same calendar: untouched

    out = await sync_all(pool, real_cal, None, now=NOW)                                       # a different calendar
    assert out["push"] == {"pushed": 1} and [e.title for e in real_cal.events.values()] == ["zz moves calendars"]
    assert "update" not in real_cal.calls                                                      # no failing updates of foreign ids
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM review_state WHERE key = 'gcal'")
