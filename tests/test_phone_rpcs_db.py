"""The functions the phone calls (migration add_phone_rpcs), against the test database. Rows start with `zz`."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import storage

NOW = datetime(2001, 1, 2, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    yield p
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM notes WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM statements WHERE text LIKE 'zz%'")
    await p.close()


async def capture(pool, cid, kind, text, due=None, at=NOW):
    async with pool.connection() as conn:
        await conn.execute("SELECT capture_item(%s, %s, %s, %s, %s)", (cid, kind, text, due, at))


async def one(pool, sql, *args):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()


async def count(pool, sql, *args):
    return (await one(pool, sql, *args))[0]


async def test_note_is_saved_once_and_feeds_the_extractor(pool):
    cid = uuid.uuid4()
    await capture(pool, cid, "note", "zz call the bank")
    await capture(pool, cid, "note", "zz call the bank")          # a retry
    assert await count(pool, "SELECT count(*) FROM notes WHERE client_id = %s", cid) == 1
    assert await count(pool, "SELECT count(*) FROM statements WHERE text = 'zz call the bank'") == 1
    row = await one(pool, "SELECT source, embedding_status, tags FROM notes WHERE client_id = %s", cid)
    assert row[0] == "phone" and row[1] == "pending" and row[2] == []


async def test_journal_is_tagged_and_recorded_as_journal(pool):
    cid = uuid.uuid4()
    await capture(pool, cid, "journal", "zz tired today")
    assert (await one(pool, "SELECT tags FROM notes WHERE client_id = %s", cid))[0] == ["journal"]
    assert (await one(pool, "SELECT source FROM statements WHERE text = 'zz tired today'"))[0] == "journal"


async def test_edit_changes_text_and_requeues_embedding(pool):
    cid = uuid.uuid4()
    await capture(pool, cid, "note", "zz first")
    async with pool.connection() as conn:
        await conn.execute("UPDATE notes SET embedding_status = 'done' WHERE client_id = %s", (cid,))
    await capture(pool, cid, "note", "zz second")
    row = await one(pool, "SELECT text, embedding_status FROM notes WHERE client_id = %s", cid)
    assert row == ("zz second", "pending")
    assert await count(pool, "SELECT count(*) FROM statements WHERE text IN ('zz first','zz second')") == 1


async def test_task_gets_a_due_date_and_default(pool):
    a, b = uuid.uuid4(), uuid.uuid4()
    due = NOW + timedelta(days=3)
    await capture(pool, a, "task", "zz with date", due=due)
    await capture(pool, b, "task", "zz no date")
    assert (await one(pool, "SELECT due_at FROM tasks WHERE client_id = %s", a))[0] == due
    assert (await one(pool, "SELECT due_at FROM tasks WHERE client_id = %s", b))[0] == NOW + timedelta(hours=24)


async def test_editing_a_task_keeps_its_date_when_none_is_sent(pool):
    cid = uuid.uuid4()
    due = NOW + timedelta(days=2)
    await capture(pool, cid, "task", "zz keep date", due=due)
    await capture(pool, cid, "task", "zz keep date edited")
    row = await one(pool, "SELECT text, due_at FROM tasks WHERE client_id = %s", cid)
    assert row == ("zz keep date edited", due)


async def test_converting_between_note_and_task_moves_the_row(pool):
    cid = uuid.uuid4()
    await capture(pool, cid, "note", "zz convert me")
    await capture(pool, cid, "task", "zz convert me")
    assert await count(pool, "SELECT count(*) FROM notes WHERE client_id = %s", cid) == 0
    assert await count(pool, "SELECT count(*) FROM tasks WHERE client_id = %s", cid) == 1
    await capture(pool, cid, "note", "zz convert me")
    assert await count(pool, "SELECT count(*) FROM notes WHERE client_id = %s", cid) == 1
    assert await count(pool, "SELECT count(*) FROM tasks WHERE client_id = %s", cid) == 0


async def test_delete_removes_either_kind_and_is_repeatable(pool):
    n, t = uuid.uuid4(), uuid.uuid4()
    await capture(pool, n, "note", "zz gone note")
    await capture(pool, t, "task", "zz gone task")
    for cid in (n, t, n):
        async with pool.connection() as conn:
            await conn.execute("SELECT delete_item(%s)", (cid,))
    assert await count(pool, "SELECT count(*) FROM notes WHERE text = 'zz gone note'") == 0
    assert await count(pool, "SELECT count(*) FROM tasks WHERE text = 'zz gone task'") == 0


async def test_bad_input_is_refused(pool):
    for kind, text in (("note", "   "), ("nonsense", "zz x")):
        with pytest.raises(Exception):
            await capture(pool, uuid.uuid4(), kind, text)


async def search(pool, q, limit=50):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT kind, text FROM search_notes(%s, %s)", (q, limit))
            return await cur.fetchall()


async def test_search_matches_any_word_ignoring_case_and_wildcards(pool):
    await capture(pool, uuid.uuid4(), "note", "zz Tennis with Rahul on Friday")
    await capture(pool, uuid.uuid4(), "journal", "zz 100% tired")
    await capture(pool, uuid.uuid4(), "note", "zz groceries")
    texts = [t for _, t in await search(pool, "ZZ tennis dentist")]
    assert "zz Tennis with Rahul on Friday" in texts
    assert "zz groceries" in texts                      # 'zz' alone matches every zz row: any word is enough
    only = [t for _, t in await search(pool, "tennis")]
    assert any("Tennis" in t for t in only) and not any("groceries" in t for t in only)
    percent = await search(pool, "100%")
    assert [k for k, t in percent if "100%" in t] == ["journal"]
    assert not any("groceries" in t for _, t in percent)  # a typed % is a literal, not a wildcard


async def test_empty_search_returns_the_newest(pool):
    await capture(pool, uuid.uuid4(), "note", "zz newest", at=datetime.now(timezone.utc))
    rows = await search(pool, "  ", 5)
    assert len(rows) <= 5 and rows[0][1] == "zz newest"


async def upload(pool, rows):
    import json
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT upload_usage(%s::jsonb)", (json.dumps(rows),))
            return (await cur.fetchone())[0]


@pytest.fixture
async def usage_cleanup(pool):
    yield
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM events WHERE dedupe_key LIKE 'android:zz%'")


async def test_usage_rows_are_stored_as_phone_events_and_only_once(pool, usage_cleanup):
    rows = [
        {"key": "zz1", "app": "com.google.android.youtube", "start": "2001-01-02T09:00:00Z", "end": "2001-01-02T09:10:00Z"},
        {"key": "zz2", "app": "com.google.android.apps.maps", "start": "2001-01-02T09:10:00Z", "end": "2001-01-02T09:12:30Z"},
    ]
    assert await upload(pool, rows) == 2
    assert await upload(pool, rows) == 0                      # the same batch again changes nothing
    row = await one(pool, "SELECT source, kind, app, payload, ts_end - ts_start FROM events WHERE dedupe_key = 'android:zz1'")
    assert row[:4] == ("android", "phone_app_focus", "com.google.android.youtube", {})
    assert row[4] == timedelta(minutes=10)


async def test_usage_skips_bad_rows_and_keeps_the_good_ones(pool, usage_cleanup):
    rows = [
        {"key": "zzgood", "app": "a.b", "start": "2001-01-02T09:00:00Z", "end": "2001-01-02T09:01:00Z"},
        {"key": "zzbackwards", "app": "a.b", "start": "2001-01-02T09:05:00Z", "end": "2001-01-02T09:01:00Z"},
        {"key": "zznoapp", "app": "", "start": "2001-01-02T09:00:00Z", "end": "2001-01-02T09:01:00Z"},
    ]
    assert await upload(pool, rows) == 1


async def test_usage_rejects_a_non_array(pool):
    with pytest.raises(Exception):
        await upload(pool, {"key": "zzx"})


async def test_laptop_sessionizer_does_not_see_phone_rows(pool, usage_cleanup):
    await upload(pool, [{"key": "zzseen", "app": "a.b", "start": "2001-01-02T09:00:00Z", "end": "2001-01-02T09:01:00Z"}])
    assert await count(pool, "SELECT count(*) FROM events WHERE dedupe_key = 'android:zzseen' AND kind IN ('app_focus','idle')") == 0


# ---- watchdog and failure notice (add_phone_watch) ----
from datetime import date


@pytest.fixture
async def job_cleanup(pool):
    yield
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM job_run WHERE kind = 'zz'")
        await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")


async def job(pool, step, ok, day=date(2001, 1, 3), minutes_ago=30, attempt=1, error=None, detail="{}"):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO job_run (kind, step, day, ok, attempt, error, detail, finished_at) "
            "VALUES ('zz', %s, %s, %s, %s, %s, %s::jsonb, now() - make_interval(mins => %s))",
            (step, day, ok, attempt, error, detail, minutes_ago),
        )


async def ready(pool, day):
    return (await one(pool, "SELECT schedule_ready(%s)", day))[0]


async def failure(pool):
    row = await one(pool, "SELECT step, error, day FROM latest_failure() WHERE day = %s", date(2001, 1, 3))
    return row


async def test_schedule_is_ready_after_a_successful_generate_even_if_it_skipped(pool, job_cleanup):
    day = date(2001, 1, 3)
    assert await ready(pool, day) is False
    await job(pool, "generate", False, error="boom")
    assert await ready(pool, day) is False
    await job(pool, "generate", True, detail='{"skipped": "tomorrow has entries"}')
    assert await ready(pool, day) is True
    assert await ready(pool, date(2001, 1, 4)) is False


async def test_schedule_is_ready_after_the_plan_step_too(pool, job_cleanup):
    day = date(2001, 1, 3)
    await job(pool, "plan", False, error="boom")
    assert await ready(pool, day) is False
    await job(pool, "plan", True, detail='{"skipped": "day_already_planned"}')
    assert await ready(pool, day) is True


async def test_generated_blocks_make_a_day_ready_but_the_persons_own_events_do_not(pool, job_cleanup):
    async with pool.connection() as conn:
        for origin, d in (("user", 5), ("generated", 6)):
            await conn.execute(
                "INSERT INTO schedule (title, start_at, end_at, origin) VALUES ('zz gym', %s, %s, %s)",
                (datetime(2001, 1, d, 3, 0, tzinfo=timezone.utc), datetime(2001, 1, d, 4, 0, tzinfo=timezone.utc), origin),
            )
    assert await ready(pool, date(2001, 1, 5)) is False       # a dentist pulled from Google is not a plan
    assert await ready(pool, date(2001, 1, 6)) is True        # 08:30 IST on the 6th


async def test_a_failure_is_reported_only_when_it_is_the_last_word_and_old_enough(pool, job_cleanup):
    await job(pool, "generate", False, minutes_ago=2, error="too fresh")
    assert await failure(pool) is None                          # the in-run retry may still be coming
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM job_run WHERE kind = 'zz'")
    await job(pool, "generate", False, minutes_ago=30, attempt=1, error="first try")
    await job(pool, "generate", True, minutes_ago=29, attempt=2)
    assert await failure(pool) is None                          # the retry worked
    await job(pool, "extract", False, minutes_ago=25, error="Gemini 503")
    assert await failure(pool) == ("extract", "Gemini 503", date(2001, 1, 3))
    await job(pool, "extract", True, minutes_ago=20)
    assert await failure(pool) is None                          # a later success clears it
