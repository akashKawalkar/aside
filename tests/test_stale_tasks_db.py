"""Stale tasks are dropped (kept with a reason), not deleted. Test rows are named `zz...`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import storage
from sessions.task_lifecycle import stale_reason

NOW = datetime(2031, 3, 10, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("due,slips,expected", [
    (NOW - timedelta(days=3, hours=23), 0, None),
    (NOW - timedelta(days=4, hours=1), 0, "overdue by more than 4 days"),
    (NOW + timedelta(days=1), 1, None),
    (NOW + timedelta(days=1), 2, "postponed 2 times"),
    (None, 0, None),
])
def test_stale_reason(due, slips, expected):
    assert stale_reason(due, slips, NOW) == expected


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
            await cur.execute("DELETE FROM task_log WHERE snapshot->>'text' LIKE 'zz%'")
            await cur.execute("DELETE FROM task_due_log WHERE task_id IN (SELECT id FROM tasks WHERE text LIKE 'zz%')")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
    await p.close()


async def test_postponing_counts_slips_and_two_slips_drop_the_task(pool):
    t = await storage.create_task(pool, text="zz slipper", due_at=NOW + timedelta(days=1))
    await storage.update_task(pool, task_id=t["id"], due_at=NOW + timedelta(days=2))
    await storage.update_task(pool, task_id=t["id"], due_at=NOW + timedelta(hours=30))   # earlier: not a slip
    assert await storage.drop_stale_tasks(pool, now=NOW) == []
    await storage.update_task(pool, task_id=t["id"], due_at=NOW + timedelta(days=3))

    dropped = await storage.drop_stale_tasks(pool, now=NOW)
    assert [d["text"] for d in dropped] == ["zz slipper"] and dropped[0]["dropped_reason"] == "postponed 2 times"
    assert await storage.list_tasks(pool, status="pending") == [x for x in await storage.list_tasks(pool, status="pending") if x["text"] != "zz slipper"]
    assert any(e["action"] == "dropped" and e["snapshot"]["text"] == "zz slipper" for e in await storage.list_task_log(pool))
    assert await storage.drop_stale_tasks(pool, now=NOW) == []   # rerun is a no-op


async def test_long_overdue_task_is_dropped_and_recent_one_kept(pool):
    old = await storage.create_task(pool, text="zz old", due_at=NOW - timedelta(days=5))
    new = await storage.create_task(pool, text="zz recent", due_at=NOW - timedelta(days=2))

    dropped = {d["id"]: d for d in await storage.drop_stale_tasks(pool, now=NOW)}
    assert old["id"] in dropped and new["id"] not in dropped
    assert dropped[old["id"]]["dropped_reason"] == "overdue by more than 4 days"
