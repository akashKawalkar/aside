"""Practiced schedule storage and the day build, against the test database. Rows are tagged zz / dated 2001 and removed after."""
from __future__ import annotations

import json
from datetime import date, datetime

from psycopg.types.json import Jsonb

import pytest

import storage
from digital.builder import IST
from llm.fake import FakeClient
from practiced import store
from practiced.builder import Tag

DAY = date(2001, 3, 4)


def t(h, m=0):
    return datetime(2001, 3, 4, h, m, tzinfo=IST)


async def _clean(conn):
    await conn.execute("DELETE FROM events WHERE dedupe_key LIKE 'zz-%'")
    await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
    await conn.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
    await conn.execute("DELETE FROM title_tag WHERE title LIKE 'zz%'")
    await conn.execute("DELETE FROM practiced_day WHERE day BETWEEN '2001-03-01' AND '2001-03-09'")
    await conn.execute("DELETE FROM phone_answer WHERE payload ? 'zz'")


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    async with p.connection() as conn:
        await _clean(conn)
    yield p
    async with p.connection() as conn:
        await _clean(conn)
    await p.close()


async def test_tags_are_cached_and_a_user_tag_is_never_overwritten(pool):
    await store.save_tags(pool, {"zz run": Tag((), False)}, origin="user")
    await store.save_tags(pool, {"zz run": Tag(("coding",), True), "zz code": Tag(("coding",), True)})
    got = await store.load_tags(pool, ["zz run", "zz code", "zz none"])
    assert got == {"zz run": Tag((), False), "zz code": Tag(("coding",), True)}


async def test_only_new_titles_cost_a_model_call(pool):
    await store.save_tags(pool, {"zz old": Tag(("study",), True)})
    c = FakeClient([json.dumps({"tags": [{"title": "zz new", "reason": "x", "expected": ["coding"], "observable": True}]})])
    tags = await store.ensure_tags(pool, ["zz old", "zz new", "zz new"], c)
    assert set(tags) == {"zz old", "zz new"} and len(c.calls) == 1
    assert json.loads(c.calls[0].messages[-1].content) == ["zz new"]
    await store.ensure_tags(pool, ["zz old", "zz new"], c)
    assert len(c.calls) == 1


async def test_no_client_means_only_cached_tags(pool):
    assert await store.ensure_tags(pool, ["zz nothing"], None) == {}


async def test_build_day_from_events_calendar_tasks_and_tags(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO events (source, kind, app, window_title, ts_start, ts_end, dedupe_key) "
            "VALUES ('laptop', 'app_focus', 'Code.exe', 'a.py - proj - Visual Studio Code', %s, %s, 'zz-1')", (t(10), t(11)))
        await conn.execute("INSERT INTO schedule (title, start_at, end_at, origin) VALUES ('zz Deep work', %s, %s, 'user')", (t(10), t(12)))
        await conn.execute("INSERT INTO schedule (title, start_at, end_at, origin, deleted_at) VALUES ('zz gone', %s, %s, 'user', now())", (t(14), t(15)))
        await conn.execute("INSERT INTO tasks (text, status, completed_at) VALUES ('zz done', 'done', %s)", (t(10, 30),))
        await conn.execute("INSERT INTO tasks (text, status, slip_count) VALUES ('zz slips', 'pending', 3)")
    await store.save_tags(pool, {"zz Deep work": Tag(("coding",), True)})
    r = await store.build_day(pool, DAY)
    assert [p["title"] for p in r["plan"]] == ["zz Deep work"]            # the deleted block is not on the plan
    assert r["plan"][0]["verdict"] == "PARTIAL" and r["plan"][0]["on_plan_min"] == 60
    assert [x["text"] for x in r["tasks"]["done"]] == ["zz done"]
    assert "zz slips" in [x["text"] for x in r["tasks"]["slipping"]]


async def test_saving_a_day_twice_keeps_the_digest_only_when_nothing_changed(pool):
    await store.save_practiced(pool, DAY, {"a": 1})
    async with pool.connection() as conn:
        await conn.execute("UPDATE practiced_day SET digest = '{\"d\": 1}', digest_at = now() WHERE day = %s", (DAY,))
    await store.save_practiced(pool, DAY, {"a": 1})
    assert (await store.get_practiced(pool, DAY))["digest"] == {"d": 1}
    await store.save_practiced(pool, DAY, {"a": 2})
    got = await store.get_practiced(pool, DAY)
    assert got["data"] == {"a": 2} and got["digest"] is None


async def test_answers_and_the_day_context_are_loaded_for_the_right_day(pool):
    import uuid
    async with pool.connection() as conn:
        for kind, at, day, payload in [
            ("distraction", t(10, 30), DAY, {"zz": 1, "reason": "break"}),
            ("distraction", t(10, 30), date(2001, 3, 5), {"zz": 1, "reason": "other day"}),
            ("anomaly", t(9), date(2001, 3, 5), {"zz": 1, "for_day": "2001-03-04", "label": "travel"}),
        ]:
            await conn.execute("INSERT INTO phone_answer (client_id, kind, answered_at, day, payload) VALUES (%s, %s, %s, %s, %s)",
                               (uuid.uuid4(), kind, at, day, Jsonb(payload)))
    answers, context = await store.load_answers(pool, DAY)
    assert [a["payload"]["reason"] for a in answers] == ["break"] and context == "travel"
    assert await store.load_answers(pool, date(2001, 3, 2)) == ([], None)


async def test_backfill_builds_only_missing_days_and_history_reads_them_back(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO events (source, kind, app, window_title, ts_start, ts_end, dedupe_key) "
            "VALUES ('laptop', 'app_focus', 'Code.exe', 'a.py - proj - Visual Studio Code', %s, %s, 'zz-bf')", (t(10), t(11)))
    now = datetime(2001, 3, 6, 10, tzinfo=IST)
    first = await store.backfill(pool, None, now, days=3)
    assert first["built"] == ["2001-03-04"] and sorted(first["empty"]) == ["2001-03-03", "2001-03-05"]
    assert (await store.backfill(pool, None, now, days=3))["built"] == []
    hist = await store.load_history(pool, date(2001, 3, 6), days=5)
    assert [h["day"] for h in hist] == ["2001-03-04"] and hist[0]["stats"]["productive_laptop_min"] == 60
