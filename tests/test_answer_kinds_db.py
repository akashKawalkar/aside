"""Task checks and debriefs as answer kinds, and plan blocks with their task link (migration add_answer_kinds), against
the test database. Rows sit in year 2001 or are titled `zz` and are removed afterwards."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import storage

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2001, 1, 10)


def ist(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


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


async def _clean(p):
    async with p.connection() as conn:
        await conn.execute("DELETE FROM phone_answer WHERE day BETWEEN '2001-01-01' AND '2001-01-31'")
        await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
        await conn.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")


async def upload(pool, rows):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT upload_answers(%s::jsonb)", (json.dumps(rows),))
            return (await cur.fetchone())[0]


async def stored(pool, client_id):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT kind, payload FROM phone_answer WHERE client_id = %s", (uuid.UUID(client_id),))
            return await cur.fetchone()


async def test_task_check_and_debrief_are_stored_under_their_own_kind(pool):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    at = ist(2001, 1, 10, 10).isoformat()
    assert await upload(pool, [
        {"id": a, "kind": "task_check", "at": at, "payload": {"title": "zz report", "status": "starting_now"}},
        {"id": b, "kind": "debrief", "at": at, "payload": {"title": "zz deep", "outcome": "finished"}},
    ]) == 2
    assert await stored(pool, a) == ("task_check", {"title": "zz report", "status": "starting_now"})
    assert await stored(pool, b) == ("debrief", {"title": "zz deep", "outcome": "finished"})


async def test_a_tap_carrying_a_subkind_is_stored_as_that_kind(pool):
    """How the phone sends them, so it works before and after this migration."""
    a, t = str(uuid.uuid4()), str(uuid.uuid4())
    at = ist(2001, 1, 10, 10).isoformat()
    assert await upload(pool, [
        {"id": a, "kind": "tap", "at": at, "payload": {"subkind": "task_check", "title": "zz report", "status": "blocked"}},
        {"id": t, "kind": "tap", "at": at, "payload": {"subkind": "mood", "event": "opened"}},   # not one we know: stays a tap
    ]) == 2
    assert await stored(pool, a) == ("task_check", {"title": "zz report", "status": "blocked"})
    assert await stored(pool, t) == ("tap", {"subkind": "mood", "event": "opened"})


async def test_unknown_kinds_are_still_skipped(pool):
    assert await upload(pool, [{"id": str(uuid.uuid4()), "kind": "mood", "at": ist(2001, 1, 10, 9).isoformat()}]) == 0


async def test_plan_blocks_carry_the_task_and_its_slips(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO tasks (text, slip_count) VALUES ('zz write report', 3) RETURNING id")
            task_id = (await cur.fetchone())[0]
        for title, h, tid in [("zz report", 9, task_id), ("zz lunch", 13, None)]:
            await conn.execute(
                "INSERT INTO schedule (title, start_at, end_at, origin, edited_by_user, task_id) VALUES (%s, %s, %s, 'generated', false, %s)",
                (title, ist(2001, 1, 10, h), ist(2001, 1, 10, h + 1), tid),
            )
        async with conn.cursor() as cur:
            await cur.execute("SELECT title, task_id, slip_count FROM plan_blocks(%s) WHERE title LIKE 'zz%%'", (DAY,))
            assert await cur.fetchall() == [("zz report", task_id, 3), ("zz lunch", None, 0)]
