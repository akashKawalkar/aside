"""The digest run-once job, against the test database. Rows are tagged zz / dated 2001 and removed after."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

import storage
from digital.builder import IST
from llm.fake import FakeClient
from practiced import store
from practiced.builder import Tag
from practiced.digest import DigestError


DAY = datetime(2001, 3, 4, tzinfo=IST).date()


def t(h, m=0):
    return datetime(2001, 3, 4, h, m, tzinfo=IST)


NOW = datetime(2001, 3, 6, 10, 0, tzinfo=IST)


def test_finished_days_are_the_logical_days_that_are_over():
    assert [d.isoformat() for d in store.finished_days(datetime(2001, 3, 6, 10, 0, tzinfo=IST), 3)] == ["2001-03-03", "2001-03-04", "2001-03-05"]
    assert store.finished_days(datetime(2001, 3, 6, 4, 0, tzinfo=IST), 1)[0].isoformat() == "2001-03-04"       # before 05:00 yesterday is not over


async def _clean(conn):
    await conn.execute("DELETE FROM events WHERE dedupe_key LIKE 'zz-%'")
    await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
    await conn.execute("DELETE FROM title_tag WHERE title LIKE 'zz%'")
    await conn.execute("DELETE FROM practiced_day WHERE day BETWEEN '2001-03-01' AND '2001-03-09'")


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception as e:
        pytest.skip(f"database not reachable: {e!r}")
    async with p.connection() as conn:
        await _clean(conn)
        await conn.execute(
            "INSERT INTO events (source, kind, app, window_title, ts_start, ts_end, dedupe_key) "
            "VALUES ('laptop', 'app_focus', 'Code.exe', 'a.py - proj - Visual Studio Code', %s, %s, 'zz-1')", (t(10), t(11)))
        await conn.execute("INSERT INTO schedule (title, start_at, end_at, origin) VALUES ('zz Deep work', %s, %s, 'user')", (t(10), t(12)))
    await store.save_tags(p, {"zz Deep work": Tag(("coding",), True)})
    yield p
    async with p.connection() as conn:
        await _clean(conn)
    await p.close()


def answer(note="ok"):
    return json.dumps({"plan_blocks": [{"title": "zz Deep work", "note": note}], "time_sinks": [], "best_stretch": "10:00-11:00 coding",
                       "anomalies": [], "summary": "A short day.", "title_tags": []})


async def test_the_job_digests_a_finished_day_once_and_skips_empty_days(pool):
    c = FakeClient([answer()])
    out = await store.run_digest_once(pool, c, NOW)
    assert out["digested"] == ["2001-03-04"] and sorted(out["empty"]) == ["2001-03-03", "2001-03-05"]
    row = await store.get_practiced(pool, DAY)
    assert row["digest"]["facts"]["plan_blocks"][0]["verdict"] == "PARTIAL" and row["data"]["plan"][0]["on_plan_min"] == 60

    again = await store.run_digest_once(pool, c, NOW)
    assert again["digested"] == [] and len(c.calls) == 1                         # cached: no second model call


async def test_editing_the_calendar_afterwards_regenerates_the_digest(pool):
    c = FakeClient([answer("first"), answer("second")])
    await store.run_digest_once(pool, c, NOW)
    async with pool.connection() as conn:
        await conn.execute("UPDATE schedule SET end_at = %s WHERE title = 'zz Deep work'", (t(11),))
    out = await store.run_digest_once(pool, c, NOW)
    assert out["digested"] == ["2001-03-04"] and len(c.calls) == 2
    assert (await store.get_practiced(pool, DAY))["digest"]["facts"]["plan_blocks"][0]["note"] == "second"


async def test_a_model_failure_raises_and_leaves_the_schedule_built_but_undigested(pool):
    with pytest.raises(DigestError):
        await store.run_digest_once(pool, FakeClient(["nope"]), NOW)
    row = await store.get_practiced(pool, DAY)
    assert row["data"]["plan"] and row["digest"] is None
