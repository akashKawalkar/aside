"""Integration tests for the data-quality tables against the real database.
Rows are tagged `zz` (titles) or dated 2001 (log tables with no text column), and removed afterwards.
They are skipped if the database is unreachable."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import storage
from config import Retention

OLD = datetime(2001, 1, 1, 12, 0, tzinfo=timezone.utc)
OLD_DAY = date(2001, 1, 1)


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    yield p
    await p.close()


async def q(pool, sql, params=()):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            return await cur.fetchall() if cur.description else None


@pytest.fixture
async def schedule_ids(pool):
    ids: list[int] = []
    yield ids
    if ids:
        await q(pool, "DELETE FROM schedule_log WHERE schedule_id = ANY(%s)", (ids,))
        await q(pool, "DELETE FROM schedule WHERE id = ANY(%s)", (ids,))


# ---------- schedule log, origin, edited flag ----------

async def test_schedule_create_edit_delete_are_logged_with_before_and_after(pool, schedule_ids):
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=400)
    entry = await storage.create_schedule_entry(pool, title="zz gym", start_at=start, end_at=start + timedelta(hours=1))
    schedule_ids.append(entry["id"])
    assert (entry["origin"], entry["edited_by_user"]) == ("user", False)

    await storage.update_schedule_entry(pool, entry["id"], title="zz gym 2", start_at=start, end_at=start + timedelta(hours=2))
    assert await storage.delete_schedule_entry(pool, entry["id"]) is True
    assert await storage.delete_schedule_entry(pool, entry["id"]) is False                 # already gone: nothing logged
    assert await storage.update_schedule_entry(pool, entry["id"], title="x", start_at=start, end_at=start + timedelta(hours=1)) is None

    rows = await q(pool, "SELECT action, before, after FROM schedule_log WHERE schedule_id = %s ORDER BY id", (entry["id"],))
    assert [r[0] for r in rows] == ["create", "edit", "delete"]
    create, edit, delete = rows
    assert create[1] is None and create[2]["title"] == "zz gym"
    assert edit[1]["title"] == "zz gym" and edit[2]["title"] == "zz gym 2" and edit[2]["end_at"] != edit[1]["end_at"]
    assert delete[1]["title"] == "zz gym 2" and delete[2] is None


async def test_editing_a_generated_entry_marks_it_edited_by_user(pool, schedule_ids):
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=401)
    entry = await storage.create_schedule_entry(pool, title="zz draft", start_at=start, end_at=start + timedelta(hours=1), origin="generated")
    schedule_ids.append(entry["id"])
    assert (entry["origin"], entry["edited_by_user"]) == ("generated", False)

    edited = await storage.update_schedule_entry(pool, entry["id"], title="zz draft", start_at=start, end_at=start + timedelta(hours=2))
    assert (edited["origin"], edited["edited_by_user"]) == ("generated", True)
    [(_, _, after)] = await q(pool, "SELECT action, before, after FROM schedule_log WHERE schedule_id = %s AND action = 'edit'", (entry["id"],))
    assert after["edited_by_user"] is True


async def test_an_unknown_origin_is_rejected_by_the_database(pool):
    start = datetime.now(timezone.utc) + timedelta(days=402)
    with pytest.raises(Exception, match="ck_schedule_origin"):
        await storage.create_schedule_entry(pool, title="zz bad", start_at=start, end_at=start + timedelta(hours=1), origin="robot")


# ---------- task due log ----------

async def test_only_real_due_date_changes_are_logged(pool):
    due = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    task = await storage.create_task(pool, text="zz slipping", due_at=due)
    try:
        await storage.update_task(pool, task_id=task["id"], text="zz slipping renamed")        # no due change
        await storage.update_task(pool, task_id=task["id"], due_at=due)                         # same due: not a change
        await storage.update_task(pool, task_id=task["id"], due_at=due + timedelta(days=1))
        await storage.update_task(pool, task_id=task["id"], text="zz again", due_at=due + timedelta(days=2))

        rows = await q(pool, "SELECT old_due, new_due FROM task_due_log WHERE task_id = %s ORDER BY id", (task["id"],))
        assert rows == [(due, due + timedelta(days=1)), (due + timedelta(days=1), due + timedelta(days=2))]
    finally:
        await q(pool, "DELETE FROM task_due_log WHERE task_id = %s", (task["id"],))
        await q(pool, "DELETE FROM tasks WHERE id = %s", (task["id"],))


# ---------- monitoring and heartbeat logs ----------

async def test_monitoring_log_includes_the_switch_before_the_window(pool):
    try:
        for hours, enabled in ((0, True), (3, False), (6, True)):
            await q(pool, "INSERT INTO monitoring_log (enabled, created_at) VALUES (%s, %s)", (enabled, OLD + timedelta(hours=hours)))
        got = await storage.list_monitoring_log(pool, start=OLD + timedelta(hours=4), end=OLD + timedelta(hours=8))
        assert [(g["enabled"], g["at"]) for g in got] == [(False, OLD + timedelta(hours=3)), (True, OLD + timedelta(hours=6))]
    finally:
        await q(pool, "DELETE FROM monitoring_log WHERE created_at < '2002-01-01'")


async def test_heartbeat_samples_round_trip_in_a_window(pool):
    try:
        for minutes, collector in ((0, True), (1, False), (30, True)):
            await q(pool, "INSERT INTO heartbeat_log (ts, collector_ok, ingestor_ok) VALUES (%s, %s, true)", (OLD + timedelta(minutes=minutes), collector))
        got = await storage.list_heartbeats(pool, start=OLD, end=OLD + timedelta(minutes=10))
        assert [(h["collector_ok"], h["ingestor_ok"]) for h in got] == [(True, True), (False, True)]
    finally:
        await q(pool, "DELETE FROM heartbeat_log WHERE ts < '2002-01-01'")


# ---------- snapshot, day record, mark-wrong ----------

async def test_first_snapshot_and_day_record_of_a_day_win(pool):
    try:
        assert await storage.save_schedule_snapshot(pool, day=OLD_DAY, entries=[{"title": "zz one"}], late=True) is True
        assert await storage.save_schedule_snapshot(pool, day=OLD_DAY, entries=[{"title": "zz two"}], late=False) is False
        assert await storage.get_schedule_snapshot(pool, day=OLD_DAY) == [{"title": "zz one"}]
        assert await storage.get_schedule_snapshot(pool, day=OLD_DAY + timedelta(days=1)) is None
        assert OLD_DAY in await storage.days_with_snapshot(pool, first=OLD_DAY, last=OLD_DAY)

        assert await storage.save_day_record(pool, day=OLD_DAY, data={"zz": 1}, late=False) is True
        assert await storage.save_day_record(pool, day=OLD_DAY, data={"zz": 2}, late=False) is False
        assert (await q(pool, "SELECT data, late FROM day_record WHERE day = %s", (OLD_DAY,))) == [({"zz": 1}, False)]
        assert OLD_DAY in await storage.days_with_day_record(pool, first=OLD_DAY, last=OLD_DAY)
    finally:
        await q(pool, "DELETE FROM schedule_snapshot WHERE day = %s", (OLD_DAY,))
        await q(pool, "DELETE FROM day_record WHERE day = %s", (OLD_DAY,))


async def test_mark_wrong_logs_the_target_and_keeps_the_compile_id(pool):
    compile_id = await storage.insert_compile_log(pool, {
        "ts": OLD, "situation": "chat", "recipe_name": "chat", "recipe_hash": "zz", "model": "fake",
        "offered": [], "chosen": [], "dropped": [], "tokens": 0, "query": "zz"})
    mark_id = await storage.insert_mark_wrong(pool, target="zz reply", compile_log_id=compile_id, reason="not Tuesday")
    try:
        assert await q(pool, "SELECT target, compile_log_id, reason FROM mark_wrong_log WHERE id = %s", (mark_id,)) == [("zz reply", compile_id, "not Tuesday")]
    finally:
        await q(pool, "DELETE FROM mark_wrong_log WHERE id = %s", (mark_id,))
        await q(pool, "DELETE FROM compile_log WHERE id = %s", (compile_id,))


# ---------- retention ----------

async def test_prune_reports_by_default_and_deletes_only_with_apply(pool):
    tables = ("events", "schedule", "compile_log", "llm_trace", "heartbeat_log")
    before = {t: (await q(pool, f"SELECT count(*) FROM {t}"))[0][0] for t in tables}
    now = datetime.now(timezone.utc)
    try:
        await q(pool, "INSERT INTO heartbeat_log (ts, collector_ok, ingestor_ok) VALUES (%s, true, true)", (OLD,))

        report = await storage.prune(pool, Retention(), now=now)                      # every rule, report only
        assert report["heartbeat samples"] >= 1 and set(report) >= {"raw events", "past schedule entries", "compile logs"}
        after = {t: (await q(pool, f"SELECT count(*) FROM {t}"))[0][0] for t in tables}
        assert after == {**before, "heartbeat_log": before["heartbeat_log"] + 1}      # nothing was deleted

        done = await storage.prune(pool, Retention(), now=now, apply=True, names={"heartbeat samples"})
        assert list(done) == ["heartbeat samples"] and done["heartbeat samples"] >= 1
        assert (await q(pool, "SELECT count(*) FROM heartbeat_log WHERE ts < '2002-01-01'"))[0][0] == 0
        assert (await q(pool, "SELECT count(*) FROM events"))[0][0] == before["events"]   # other rules were not run
    finally:
        await q(pool, "DELETE FROM heartbeat_log WHERE ts < '2002-01-01'")


async def test_sessions_no_longer_depend_on_their_first_event(pool):
    rows = await q(pool, "SELECT 1 FROM pg_constraint WHERE conname = 'fk_sessions_first_event_id'")
    assert rows == []
    rows = await q(pool, "SELECT 1 FROM pg_constraint WHERE conname = 'uq_sessions_first_event_id'")
    assert rows == [(1,)]
