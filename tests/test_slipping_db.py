"""Slipping tasks: the streak rule (pure) and the three-days-running question, against the test database
(migration add_task_question). Rows are `zz`-texted tasks with schedule blocks in 2031 and are removed afterwards."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import storage
from practiced.slipping import MAX_PER_NIGHT, run_slipping_once, streak

IST = timezone(timedelta(hours=5, minutes=30))
LAST = date(2031, 3, 4)                                 # the day that ended
NOW = datetime(2031, 3, 5, 2, 0, tzinfo=IST)            # the 02:00 night run
FOR_DAY = date(2031, 3, 5)


def d(n):
    return LAST - timedelta(days=n)


# ---------- the rule, pure ----------

def test_streak_counts_consecutive_days_ending_at_the_last_day():
    assert streak({d(0), d(1), d(2)}, LAST) == 3
    assert streak({d(0), d(1), d(3)}, LAST) == 2           # a gap ends it
    assert streak({d(1), d(2), d(3)}, LAST) == 0           # the last day itself must count
    assert streak(set(), LAST) == 0


# ---------- the nightly question, against the database ----------

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
        await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
        await conn.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")          # task_question rows go with their task


async def task(pool, text, *, status="pending", someday=False):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO tasks (text, status, due_at, completed_at, someday_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (text, status, NOW, NOW if status == "completed" else None, NOW if someday else None))
            return (await cur.fetchone())[0]


async def block(pool, task_id, day, *, deleted=False):
    start = datetime(day.year, day.month, day.day, 10, 0, tzinfo=IST)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO schedule (title, start_at, end_at, origin, task_id, deleted_at) VALUES (%s, %s, %s, 'generated', %s, %s)",
            (f"zz block {task_id}", start, start + timedelta(hours=1), task_id, NOW if deleted else None))


async def slipped(pool, text, days=(0, 1, 2)):
    t = await task(pool, text)
    for n in days:
        await block(pool, t, d(n))
    return t


async def questions(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT t.text, q.slipped_days, q.for_day, q.status FROM task_question q JOIN tasks t ON t.id = q.task_id "
                              "WHERE t.text LIKE 'zz%' ORDER BY q.id")
            return await cur.fetchall()


async def test_a_task_planned_three_days_running_is_asked_once(pool):
    await slipped(pool, "zz three days")
    out = await run_slipping_once(pool, LAST, FOR_DAY, NOW)
    assert [a["text"] for a in out["asked"]] == ["zz three days"]
    assert await questions(pool) == [("zz three days", 3, FOR_DAY, "pending")]


async def test_it_is_not_asked_again_within_a_week(pool):
    await slipped(pool, "zz nag")
    await run_slipping_once(pool, LAST, FOR_DAY, NOW)
    again = await run_slipping_once(pool, LAST, FOR_DAY, NOW + timedelta(hours=1))         # the 03:00 retry
    assert again["asked"] == []
    later = await run_slipping_once(pool, LAST + timedelta(days=3), FOR_DAY + timedelta(days=3), NOW + timedelta(days=3))
    assert later["asked"] == []                                                            # still inside the week
    assert len(await questions(pool)) == 1


async def test_two_days_a_gap_or_a_closed_task_are_not_asked(pool):
    await slipped(pool, "zz two days", days=(0, 1))
    await slipped(pool, "zz gap", days=(0, 2, 3))
    done = await slipped(pool, "zz done")
    async with pool.connection() as conn:
        await conn.execute("UPDATE tasks SET status = 'completed', completed_at = %s WHERE id = %s", (NOW, done))
    parked = await slipped(pool, "zz parked")
    async with pool.connection() as conn:
        await conn.execute("UPDATE tasks SET someday_at = now() WHERE id = %s", (parked,))
    gone = await task(pool, "zz removed")
    for n in (0, 1, 2):
        await block(pool, gone, d(n), deleted=True)                                        # a block taken off the calendar does not count
    assert (await run_slipping_once(pool, LAST, FOR_DAY, NOW))["asked"] == []


async def test_a_pushed_due_date_counts_as_a_slipped_day(pool):
    t = await task(pool, "zz pushed")
    for n in (1, 2):
        await block(pool, t, d(n))
    when = datetime(LAST.year, LAST.month, LAST.day, 12, 0, tzinfo=IST)
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO task_due_log (task_id, old_due, new_due, created_at) VALUES (%s, %s, %s, %s)",
                           (t, when, when + timedelta(days=1), when))
    assert [a["days"] for a in (await run_slipping_once(pool, LAST, FOR_DAY, NOW))["asked"]] == [3]


async def test_at_most_two_a_night_worst_first(pool):
    await slipped(pool, "zz a")
    four = await slipped(pool, "zz b", days=(0, 1, 2, 3))
    await slipped(pool, "zz c")
    out = await run_slipping_once(pool, LAST, FOR_DAY, NOW)
    assert len(out["asked"]) == MAX_PER_NIGHT == 2
    assert out["asked"][0]["task_id"] == four and out["asked"][0]["days"] == 4


async def test_an_unanswered_question_expires_after_three_days(pool):
    await slipped(pool, "zz ignored")
    await run_slipping_once(pool, LAST, FOR_DAY, NOW)
    out = await run_slipping_once(pool, LAST + timedelta(days=4), FOR_DAY + timedelta(days=4), NOW + timedelta(days=4))
    assert out["expired"] == 1
    assert (await questions(pool))[0][3] == "expired"


# ---------- what the phone calls ----------

async def rpc(pool, sql, args):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, args or None)
            return await cur.fetchall()


async def asked(pool, text="zz q"):
    t = await slipped(pool, text)
    await run_slipping_once(pool, LAST, FOR_DAY, datetime.now(IST))
    qid = (await rpc(pool, "SELECT id FROM task_question WHERE task_id = %s", (t,)))[0][0]
    return t, qid


async def task_row(pool, t):
    return (await rpc(pool, "SELECT text, status, due_at > now(), slip_count, someday_at IS NOT NULL, dropped_reason FROM tasks WHERE id = %s", (t,)))[0]


async def answer(pool, qid, choice, text=None):
    return (await rpc(pool, "SELECT answer_task_question(%s, %s, %s)", (qid, choice, text)))[0][0]


async def test_the_phone_lists_open_questions_for_open_tasks(pool):
    t, qid = await asked(pool)
    listed = await rpc(pool, "SELECT id, task_text, slipped_days FROM task_questions() WHERE task_text LIKE 'zz%'", ())
    assert listed == [(qid, "zz q", 3)]
    async with pool.connection() as conn:
        await conn.execute("UPDATE tasks SET status = 'completed', completed_at = now() WHERE id = %s", (t,))
    assert await rpc(pool, "SELECT id FROM task_questions() WHERE task_text LIKE 'zz%'", ()) == []       # done meanwhile: nothing to ask


async def test_keep_changes_nothing(pool):
    t, qid = await asked(pool)
    before = await task_row(pool, t)
    assert await answer(pool, qid, "keep") == "answered"
    assert await task_row(pool, t) == before
    assert await rpc(pool, "SELECT id FROM task_questions() WHERE task_text LIKE 'zz%'", ()) == []


async def test_smaller_replaces_the_text_and_moves_the_task_to_tomorrow(pool):
    t, qid = await asked(pool)
    assert await answer(pool, qid, "smaller", "  zz open the file  ") == "answered"
    text, status, due_ahead, slips, someday, _ = await task_row(pool, t)
    assert (text, status, due_ahead, slips, someday) == ("zz open the file", "pending", True, 0, False)
    assert (await rpc(pool, "SELECT original_text, answer_text, choice FROM task_question WHERE id = %s", (qid,)))[0] == ("zz q", "zz open the file", "smaller")


async def test_smaller_needs_a_step(pool):
    _, qid = await asked(pool)
    with pytest.raises(Exception, match="smaller first step"):
        await answer(pool, qid, "smaller", "   ")


async def test_drop_closes_the_task(pool):
    t, qid = await asked(pool)
    await answer(pool, qid, "drop")
    row = await task_row(pool, t)
    assert row[1] == "dropped" and "slipping-task question" in row[5]


async def test_someday_parks_the_task_and_the_planner_queries_skip_it(pool):
    t, qid = await asked(pool)
    async with pool.connection() as conn:
        await conn.execute("UPDATE tasks SET slip_count = 5 WHERE id = %s", (t,))        # stale by the old rule: it would be dropped tonight
    await answer(pool, qid, "someday")
    row = await task_row(pool, t)
    assert row[1] == "pending" and row[4] is True
    assert t not in [x["id"] for x in await storage.list_pending_with_slips(pool)]
    assert t not in [x["id"] for x in await storage.drop_stale_tasks(pool)]     # parked is never dropped as stale
    assert (await task_row(pool, t))[1] == "pending"
    assert "someday_at IS NULL" in await storage.someday_clause(pool)


async def test_a_second_answer_is_a_safe_no_op(pool):
    t, qid = await asked(pool)
    assert await answer(pool, qid, "drop") == "answered"
    assert await answer(pool, qid, "someday") == "answered"         # a retry of an answered question changes nothing
    row = await task_row(pool, t)
    assert row[1] == "dropped" and row[4] is False


async def test_an_unknown_choice_is_refused(pool):
    _, qid = await asked(pool)
    with pytest.raises(Exception, match="unknown choice"):
        await answer(pool, qid, "later")


async def test_without_the_migration_the_step_is_skipped_not_failed(pool, monkeypatch):
    """Live runs this code before the migration is applied there: the nightly job must carry on."""
    import practiced.slipping as slipping

    async def not_migrated(_pool):
        return False
    monkeypatch.setattr(slipping, "migrated", not_migrated)
    assert (await run_slipping_once(pool, LAST, FOR_DAY, NOW)) == {"skipped": "migration add_task_question is not applied"}
