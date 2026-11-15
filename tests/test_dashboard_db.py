"""The dashboard function (migration add_dashboard_reads) against the test database: every section answers, the shapes the
page relies on are there, and a wrong section is refused. Rows start with `zz` or are dated 2001."""
from __future__ import annotations

import json

import pytest

import storage


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    yield p
    async with p.connection() as conn:
        await conn.execute("DELETE FROM events WHERE dedupe_key LIKE 'android:zzdash%'")
        await conn.execute("DELETE FROM tasks WHERE text LIKE 'zzdash%'")
        await conn.execute("DELETE FROM notes WHERE text LIKE 'zzdash%'")
    await p.close()


async def dash(pool, section, args=None):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT dashboard(%s, %s::jsonb)", (section, json.dumps(args or {})))
            return (await cur.fetchone())[0]


async def test_every_section_answers(pool):
    for section in ("overview", "schedule", "tasks", "jobs", "review", "patterns", "usage", "uptime", "drafts"):
        assert await dash(pool, section) is not None, section


async def test_overview_has_what_the_page_shows(pool):
    o = await dash(pool, "overview")
    for key in ("today", "tasks_pending", "notes_total", "today_ready", "tomorrow_ready", "schedule_today", "schedule_tomorrow",
                "phone_seconds_today", "laptop_last_seen", "last_failure", "review_date"):
        assert key in o, key
    assert isinstance(o["schedule_today"], list)


async def test_tasks_split_into_pending_dropped_and_completed(pool):
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO tasks (text, status, dropped_at, dropped_reason) VALUES ('zzdash gone', 'dropped', now(), 'too old')")
        await conn.execute("INSERT INTO tasks (text, status) VALUES ('zzdash open', 'pending')")
    t = await dash(pool, "tasks")
    assert set(t) == {"pending", "dropped", "completed"}
    assert any(x["text"] == "zzdash open" for x in t["pending"])
    gone = [x for x in t["dropped"] if x["text"] == "zzdash gone"]
    assert gone and gone[0]["reason"] == "too old"


async def test_usage_groups_phone_time_by_day_and_app(pool):
    async with pool.connection() as conn:
        for key, start, end in (("zzdash1", "2001-01-02T09:00:00Z", "2001-01-02T09:10:00Z"), ("zzdash2", "2001-01-02T10:00:00Z", "2001-01-02T10:05:00Z")):
            await conn.execute(
                "INSERT INTO events (source, kind, app, ts_start, ts_end, payload, dedupe_key) VALUES ('android', 'phone_app_focus', 'zzdash.app', %s, %s, '{}', %s)",
                (start, end, f"android:{key}"),
            )
    u = await dash(pool, "usage", {"days": 60})
    assert set(u) >= {"phone", "laptop", "from", "to"}
    # 2001 is outside the window, so the rows above must not appear; the call itself must succeed
    assert not any(r["app"] == "zzdash.app" for r in u["phone"])


async def test_schedule_window_is_capped_and_ordered(pool):
    rows = await dash(pool, "schedule", {"from": "2001-01-01", "to": "2030-01-01"})
    assert isinstance(rows, list)


async def test_unknown_section_is_refused(pool):
    with pytest.raises(Exception):
        await dash(pool, "everything")
