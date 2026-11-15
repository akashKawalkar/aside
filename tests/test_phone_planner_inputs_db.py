"""Phone inputs for the planner (migration add_phone_planner_inputs), against the test database. Rows are tagged `zz`
or sit in year 2001 so they never touch real data, and are removed afterwards."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import storage

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2001, 1, 10)


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
        await conn.execute("DELETE FROM phone_answer WHERE day BETWEEN '2001-01-01' AND '2001-01-31' OR payload ? 'zz'")
        await conn.execute("DELETE FROM notes WHERE text LIKE 'zz%'")
        await conn.execute("DELETE FROM statements WHERE text LIKE 'zz%'")
        await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
        await conn.execute("DELETE FROM events WHERE dedupe_key LIKE 'zzsleep:%'")


async def one(pool, sql, *args):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


async def upload(pool, rows):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT upload_answers(%s::jsonb)", (json.dumps(rows),))
            return (await cur.fetchone())[0]


def ist(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


# --- upload_answers -------------------------------------------------------------------------------------------------

async def test_answers_are_saved_once_and_use_the_0500_day(pool):
    a = str(uuid.uuid4())
    # 03:00 IST on Jan 11 still belongs to the day that started at 05:00 on Jan 10
    row = {"id": a, "kind": "distraction", "at": ist(2001, 1, 11, 3).isoformat(), "payload": {"reason": "break", "mins": 32}}
    assert await upload(pool, [row]) == 1
    assert await upload(pool, [row]) == 0                              # a retry changes nothing
    got = await one(pool, "SELECT day, kind, payload FROM phone_answer WHERE client_id = %s", uuid.UUID(a))
    assert got[0] == DAY and got[1] == "distraction" and got[2] == {"reason": "break", "mins": 32}


async def test_unknown_kinds_and_blank_rows_are_skipped(pool):
    rows = [
        {"id": str(uuid.uuid4()), "kind": "mood", "at": ist(2001, 1, 10, 9).isoformat()},
        {"id": "", "kind": "tap", "at": ist(2001, 1, 10, 9).isoformat()},
        {"id": str(uuid.uuid4()), "kind": "tap", "at": ist(2001, 1, 10, 9).isoformat(), "payload": {"opened": True}},
    ]
    assert await upload(pool, rows) == 1


async def test_non_array_batch_is_refused(pool):
    with pytest.raises(Exception):
        async with pool.connection() as conn:
            await conn.execute("SELECT upload_answers('{}'::jsonb)")


# --- plan_blocks ----------------------------------------------------------------------------------------------------

async def test_plan_blocks_returns_live_blocks_of_the_day_in_order(pool):
    async with pool.connection() as conn:
        for title, h, deleted in [("zz late", 15, False), ("zz early", 9, False), ("zz gone", 11, True), ("zz other day", 9, None)]:
            day = 11 if deleted is None else 10
            await conn.execute(
                "INSERT INTO schedule (title, start_at, end_at, origin, edited_by_user, deleted_at) "
                "VALUES (%s, %s, %s, 'generated', false, %s)",
                (title, ist(2001, 1, day, h), ist(2001, 1, day, h + 1), datetime.now(timezone.utc) if deleted else None),
            )
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT title FROM plan_blocks(%s) WHERE title LIKE 'zz%%'", (DAY,))
            assert [r[0] for r in await cur.fetchall()] == ["zz early", "zz late"]


# --- sleep_guess ----------------------------------------------------------------------------------------------------

async def _event(conn, n, start, end, kind="app_focus", source="laptop"):
    await conn.execute(
        "INSERT INTO events (source, kind, app, ts_start, ts_end, payload, dedupe_key) VALUES (%s, %s, 'zz', %s, %s, '{}'::jsonb, %s)",
        (source, kind, start, end, f"zzsleep:{n}"),
    )


async def test_sleep_guess_is_the_longest_quiet_stretch_across_devices(pool):
    async with pool.connection() as conn:
        await _event(conn, 1, ist(2001, 1, 9, 22, 0), ist(2001, 1, 10, 2, 30))                     # laptop until 02:30
        await _event(conn, 2, ist(2001, 1, 10, 1, 0), ist(2001, 1, 10, 3, 10), source="android")   # phone until 03:10
        await _event(conn, 3, ist(2001, 1, 10, 3, 15), ist(2001, 1, 10, 3, 20), kind="idle")      # idle never counts
        await _event(conn, 4, ist(2001, 1, 10, 10, 40), ist(2001, 1, 10, 11, 0), source="android")  # up at 10:40
    got = await one(pool, "SELECT sleep_start, wake_at, minutes FROM sleep_guess(%s)", DAY)
    assert got[0] == ist(2001, 1, 10, 3, 10) and got[1] == ist(2001, 1, 10, 10, 40) and got[2] == 450


async def test_sleep_guess_is_empty_when_nothing_happened_after_the_stretch(pool):
    async with pool.connection() as conn:
        await _event(conn, 5, ist(2001, 1, 9, 18, 30), ist(2001, 1, 10, 2, 0))
    assert await one(pool, "SELECT * FROM sleep_guess(%s)", DAY) is None


async def test_sleep_guess_ignores_stretches_under_three_hours(pool):
    async with pool.connection() as conn:
        await _event(conn, 6, ist(2001, 1, 9, 18, 0), ist(2001, 1, 10, 6, 0))
        await _event(conn, 7, ist(2001, 1, 10, 8, 0), ist(2001, 1, 10, 13, 59))
    assert await one(pool, "SELECT * FROM sleep_guess(%s)", DAY) is None


# --- p: and r: notes ------------------------------------------------------------------------------------------------

async def test_plan_and_rule_notes_are_tagged_and_do_not_feed_the_extractor(pool):
    p, r = uuid.uuid4(), uuid.uuid4()
    async with pool.connection() as conn:
        await conn.execute("SELECT capture_item(%s, 'plan', 'zz no calls after 6', NULL, now())", (p,))
        await conn.execute("SELECT capture_item(%s, 'rule', 'zz meditate before dinner', NULL, now())", (r,))
    assert (await one(pool, "SELECT tags FROM notes WHERE client_id = %s", p))[0] == ["plan"]
    assert (await one(pool, "SELECT tags FROM notes WHERE client_id = %s", r))[0] == ["rule"]
    assert (await one(pool, "SELECT count(*) FROM statements WHERE text LIKE 'zz%%'"))[0] == 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT kind FROM search_notes('zz', 10) ORDER BY kind")
            assert [x[0] for x in await cur.fetchall()] == ["plan", "rule"]


async def test_plain_notes_still_feed_the_extractor(pool):
    n = uuid.uuid4()
    async with pool.connection() as conn:
        await conn.execute("SELECT capture_item(%s, 'note', 'zz still works', NULL, now())", (n,))
    assert (await one(pool, "SELECT count(*) FROM statements WHERE text = 'zz still works'"))[0] == 1


# --- anomaly_due ----------------------------------------------------------------------------------------------------

async def test_anomaly_is_asked_once_for_a_silent_day(pool):
    yesterday = (datetime.now(IST) - timedelta(hours=5)).date() - timedelta(days=1)
    async with pool.connection() as conn:
        # Real rows may already cover yesterday in a shared test database; the question is only meaningful when silent.
        quiet = (await (await conn.execute(
            "SELECT (SELECT count(*) FROM heartbeat_log WHERE ts >= %s AND ts < %s) < 30 "
            "AND (SELECT count(*) FROM events WHERE source = 'android' AND ts_start >= %s AND ts_start < %s) = 0",
            (datetime.combine(yesterday, datetime.min.time(), IST) + timedelta(hours=5),
             datetime.combine(yesterday, datetime.min.time(), IST) + timedelta(hours=29),
             datetime.combine(yesterday, datetime.min.time(), IST) + timedelta(hours=5),
             datetime.combine(yesterday, datetime.min.time(), IST) + timedelta(hours=29)))).fetchone())[0]
        await _event(conn, 9, ist(2001, 1, 1, 12), ist(2001, 1, 1, 13))                    # data from long before
    if not quiet:
        pytest.skip("the test database has activity for yesterday")
    assert (await one(pool, "SELECT anomaly_due()"))[0] == yesterday
    answer = {"id": str(uuid.uuid4()), "kind": "anomaly", "at": datetime.now(IST).isoformat(),
              "payload": {"for_day": yesterday.isoformat(), "label": "travel", "zz": True}}
    assert await upload(pool, [answer]) == 1
    assert (await one(pool, "SELECT anomaly_due()"))[0] is None
