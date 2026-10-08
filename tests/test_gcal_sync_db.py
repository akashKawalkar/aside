"""Calendar / Tasks sync against the (test) database and the in-memory fakes: add, move and delete on each side, the tie
rule, soft deletes, and the task mirror. Rows are `zz`-titled and dated 2031; removed afterwards."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import storage
from gcal.fake import FakeCalendar, FakeTasks
from gcal.model import CalEvent
from gcal.sync import IST, pull_events, push_events, sync_all, sync_tasks, default_window

NOW = datetime(2031, 3, 3, 12, 0, tzinfo=timezone.utc)
DAY = date(2031, 3, 4)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


async def _clean(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
            await cur.execute("DELETE FROM task_log WHERE snapshot->>'text' LIKE 'zz%'")
            await cur.execute("DELETE FROM task_due_log WHERE task_id IN (SELECT id FROM tasks WHERE text LIKE 'zz%')")
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


@pytest.fixture
def cal():
    return FakeCalendar()


async def sync(pool, cal, now=NOW):
    return await sync_all(pool, cal, None, now=now)


async def row_for(pool, title):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id, title, start_at, end_at, origin, edited_by_user, gcal_event_id, deleted_at, task_id "
                              "FROM schedule WHERE title = %s", (title,))
            r = await cur.fetchone()
    return dict(zip(("id", "title", "start_at", "end_at", "origin", "edited_by_user", "gcal_event_id", "deleted_at", "task_id"), r)) if r else None


async def set_updated(pool, title, when):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE schedule SET updated_at = %s WHERE title = %s", (when, title))


# ---------- events: local -> calendar ----------

async def test_local_rows_are_pushed_with_their_ids_and_a_second_sync_changes_nothing(pool, cal):
    task = await storage.create_task(pool, text="zz report", due_at=at(10))
    entry = await storage.create_schedule_entry(pool, title="zz Gym", start_at=at(7), end_at=at(8), task_id=task["id"])
    out = await sync(pool, cal)
    assert out["push"] == {"pushed": 1}
    ev = next(iter(cal.events.values()))
    assert (ev.title, ev.aside_id, ev.task_id) == ("zz Gym", entry["id"], task["id"])
    assert (await row_for(pool, "zz Gym"))["gcal_event_id"] == ev.id

    cal.calls.clear()
    again = await sync(pool, cal)
    assert again == {"pull": {}, "push": {}} and "insert" not in cal.calls and "update" not in cal.calls


async def test_a_local_edit_is_pushed_and_the_calendar_is_updated(pool, cal):
    entry = await storage.create_schedule_entry(pool, title="zz Gym", start_at=at(7), end_at=at(8))
    await sync(pool, cal)
    cal.clock.advance(minutes=30)
    await storage.update_schedule_entry(pool, entry["id"], title="zz Gym", start_at=at(8), end_at=at(9))
    await set_updated(pool, "zz Gym", cal.clock.t + timedelta(minutes=1))       # our edit is the newest thing anywhere
    out = await sync(pool, cal)

    assert out["push"] == {"pushed": 1} and out["pull"] == {}
    assert next(iter(cal.events.values())).start_at == at(8)


# ---------- events: calendar -> local ----------

async def test_an_event_added_in_google_calendar_becomes_a_user_entry(pool, cal):
    cal.user_add("zz Dentist", at(15), at(16))
    out = await sync(pool, cal)
    assert out["pull"] == {"created": 1} and out["push"] == {}
    row = await row_for(pool, "zz Dentist")
    assert (row["origin"], row["start_at"]) == ("user", at(15)) and row["gcal_event_id"] is not None


async def test_a_calendar_edit_moves_the_row_and_marks_a_generated_entry_as_the_users(pool, cal):
    await storage.create_schedule_entry(pool, title="zz Focus", start_at=at(9), end_at=at(11), origin="generated")
    await sync(pool, cal)
    event_id = next(iter(cal.events))
    cal.clock.advance(hours=1)
    cal.user_edit(event_id, start_at=at(10), end_at=at(12))

    out = await sync(pool, cal)
    row = await row_for(pool, "zz Focus")
    assert out["pull"] == {"updated": 1} and row["start_at"] == at(10) and row["edited_by_user"] is True
    assert out["push"] == {}                                                    # agreed: nothing bounces back


async def test_both_sides_edited_the_later_edit_wins(pool, cal):
    await storage.create_schedule_entry(pool, title="zz Focus", start_at=at(9), end_at=at(10))
    await sync(pool, cal)
    event_id = next(iter(cal.events))
    cal.clock.advance(hours=1)
    cal.user_edit(event_id, start_at=at(13), end_at=at(14))                     # calendar edit at T+1h
    await set_updated(pool, "zz Focus", cal.clock.t + timedelta(hours=1))       # our edit is later still (T+2h)

    out = await sync(pool, cal)
    assert out["pull"] == {} and out["push"] == {"pushed": 1}
    assert cal.events[event_id].start_at == at(9)                               # ours won and was pushed


async def test_edits_within_the_tie_window_go_to_the_calendar(pool, cal):
    await storage.create_schedule_entry(pool, title="zz Focus", start_at=at(9), end_at=at(10))
    await sync(pool, cal)
    event_id = next(iter(cal.events))
    cal.clock.advance(hours=1)
    cal.user_edit(event_id, start_at=at(13), end_at=at(14))
    await set_updated(pool, "zz Focus", cal.clock.t + timedelta(seconds=30))    # 30 s after the calendar edit: a tie

    out = await sync(pool, cal)
    assert out["pull"] == {"updated": 1} and (await row_for(pool, "zz Focus"))["start_at"] == at(13)


# ---------- deletes ----------

async def test_a_calendar_deletion_soft_deletes_the_row_and_does_not_complete_the_task(pool, cal):
    task = await storage.create_task(pool, text="zz report", due_at=at(10))
    await storage.create_schedule_entry(pool, title="zz Write report", start_at=at(9), end_at=at(11), task_id=task["id"])
    await sync(pool, cal)
    cal.clock.advance(hours=1)
    cal.user_delete(next(iter(cal.events)))

    out = await sync(pool, cal)
    row = await row_for(pool, "zz Write report")
    assert out["pull"] == {"deleted": 1} and out["push"] == {}                  # not pushed back
    assert row["deleted_at"] is not None                                        # kept as data
    assert await storage.list_schedule_range(pool, start=at(0), end=at(23, 59)) == []   # but gone from every read
    assert (await storage.get_task(pool, task_id=task["id"]))["status"] == "pending"
    assert (await sync(pool, cal)) == {"pull": {}, "push": {}}                  # and stays quiet


async def test_a_local_delete_of_a_mirrored_entry_removes_the_event_but_an_unmirrored_one_just_goes(pool, cal):
    mirrored = await storage.create_schedule_entry(pool, title="zz Mirrored", start_at=at(9), end_at=at(10))
    await sync(pool, cal)
    event_id = next(iter(cal.events))
    assert await storage.delete_schedule_entry(pool, mirrored["id"]) is True
    assert (await row_for(pool, "zz Mirrored"))["deleted_at"] is not None       # kept until the push
    async with pool.connection() as conn:                                        # the fake calendar's clock is in 2031
        async with conn.cursor() as cur:
            await cur.execute("UPDATE schedule SET deleted_at = %s WHERE title = 'zz Mirrored'", (cal.clock.t + timedelta(minutes=1),))
    assert await storage.delete_schedule_entry(pool, mirrored["id"]) is False   # already deleted as far as anyone can tell

    later = cal.clock.t + timedelta(hours=1)                                     # wall-clock time passes between syncs
    out = await sync(pool, cal, now=later)
    assert out["push"] == {"deleted": 1} and cal.events[event_id].cancelled
    assert (await sync(pool, cal, now=later + timedelta(minutes=5))) == {"pull": {}, "push": {}}

    plain = await storage.create_schedule_entry(pool, title="zz Plain", start_at=at(12), end_at=at(13))
    assert await storage.delete_schedule_entry(pool, plain["id"]) is True
    assert await row_for(pool, "zz Plain") is None                              # never mirrored: really gone


async def test_a_generated_draft_accepted_later_is_pushed_with_its_task_link(pool, cal):
    task = await storage.create_task(pool, text="zz report", due_at=at(10))
    await storage.create_schedule_entry(pool, title="zz Gen", start_at=at(9), end_at=at(10), origin="generated", task_id=task["id"])
    await sync(pool, cal)
    assert next(iter(cal.events.values())).task_id == task["id"]


# ---------- tasks ----------

async def tasks_env(pool):
    return FakeTasks()


async def test_new_tasks_are_mirrored_and_ticks_flow_both_ways(pool):
    gt = FakeTasks()
    a = await storage.create_task(pool, text="zz tick in google", due_at=at(10))
    b = await storage.create_task(pool, text="zz tick in db", due_at=at(11))
    out = await sync_tasks(pool, gt, now=NOW)
    assert out.pushed == 2 and {t.title for t in gt.tasks.values()} == {"zz tick in google", "zz tick in db"}
    assert {t.due for t in gt.tasks.values()} == {DAY}

    by_title = {t.title: t for t in gt.tasks.values()}
    gt.user_edit(by_title["zz tick in google"].id, done=True)                    # ticked on the phone
    await storage.complete_task(pool, task_id=b["id"])                           # ticked on the laptop
    out = await sync_tasks(pool, gt, now=NOW)

    assert out.completed == 1 and out.pushed == 1
    assert (await storage.get_task(pool, task_id=a["id"]))["status"] == "completed"
    assert next(t for t in gt.tasks.values() if t.title == "zz tick in db").done is True
    again = await sync_tasks(pool, gt, now=NOW)
    assert again.as_detail() == {}


async def test_a_task_made_on_the_phone_is_imported_with_an_end_of_day_due_time(pool):
    gt = FakeTasks()
    gt.user_add("zz buy milk", DAY)
    gt.user_add("zz no date")
    out = await sync_tasks(pool, gt, now=NOW)
    assert out.created == 2
    tasks = {t["text"]: t for t in await storage.list_tasks(pool, status="pending") if t["text"].startswith("zz")}
    assert tasks["zz buy milk"]["due_at"] == at(23, 59) and tasks["zz no date"]["due_at"] is None
    assert (await sync_tasks(pool, gt, now=NOW)).as_detail() == {}               # not imported twice


async def test_dropped_tasks_leave_google_tasks_and_a_delete_there_drops_the_task_here(pool):
    gt = FakeTasks()
    stale = await storage.create_task(pool, text="zz stale", due_at=at(10) - timedelta(days=9))
    gone = await storage.create_task(pool, text="zz removed on phone", due_at=at(10))
    await sync_tasks(pool, gt, now=NOW)

    await storage.drop_stale_tasks(pool, now=NOW + timedelta(days=0))            # 2031-03-04 vs due 2031-02-23: stale
    gt.tasks[next(g.id for g in gt.tasks.values() if g.title == "zz removed on phone")].deleted = True
    gt.tasks[next(g.id for g in gt.tasks.values() if g.title == "zz removed on phone")].updated = gt.clock()
    await sync_tasks(pool, gt, now=NOW)

    assert next(g for g in gt.tasks.values() if g.title == "zz stale").deleted is True
    dropped = await storage.get_task(pool, task_id=gone["id"])
    assert dropped["status"] == "dropped"


async def test_a_title_edited_on_the_phone_updates_the_task_and_a_db_edit_updates_google(pool):
    gt = FakeTasks()
    a = await storage.create_task(pool, text="zz first", due_at=at(10))
    b = await storage.create_task(pool, text="zz second", due_at=at(10))
    await sync_tasks(pool, gt, now=NOW)

    gt.clock.advance(minutes=5)
    gid = next(g.id for g in gt.tasks.values() if g.title == "zz first")
    gt.user_edit(gid, title="zz first renamed")
    await storage.update_task(pool, task_id=b["id"], text="zz second renamed")
    await sync_tasks(pool, gt, now=NOW)

    assert (await storage.get_task(pool, task_id=a["id"]))["text"] == "zz first renamed"
    assert {g.title for g in gt.tasks.values()} == {"zz first renamed", "zz second renamed"}
    assert (await sync_tasks(pool, gt, now=NOW)).as_detail() == {}


async def test_the_nightly_hooks_run_the_same_sync(pool, cal):
    from gcal.hooks import make_hooks
    await storage.create_schedule_entry(pool, title="zz Hooked", start_at=at(9), end_at=at(10))
    hooks = make_hooks(pool, cal, FakeTasks())
    assert (await hooks.pull(NOW))["events"] == {}
    assert await hooks.push(NOW) == {"pushed": 1}
