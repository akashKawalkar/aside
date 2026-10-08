"""Pattern engine: pure tests over hand-built day records (no DB, no model)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import Patterns
from patterns.engine import derive_observations
from patterns.job import due_slot
from patterns.stats import focus_hour_distribution, routine_summary, task_slip_counts

IST = ZoneInfo("Asia/Kolkata")
CFG = Patterns()


def record(day: str, titles=(), *, origin="user", edited=False, late=False, actual=None):
    entries = [{"title": t, "start_at": f"{day}T07:00:00+05:30", "end_at": f"{day}T08:00:00+05:30",
                "origin": origin, "edited_by_user": edited} for t in titles]
    timeline = [{"kind": "actual", "start": f"{day}T{a}:00+05:30", "end": f"{day}T{b}:00+05:30"} for a, b in (actual or [])]
    return {"day": day, "late": late, "data": {"schedule": entries, "review": {"timeline": timeline}}}


# 2031-03-03 is a Monday.
MON, TUE, WED, THU, FRI = (f"2031-03-0{d}" for d in range(3, 8))
NEXT_MON, NEXT_TUE, NEXT_WED = "2031-03-10", "2031-03-11", "2031-03-12"


def by_key(rows):
    return {r["observation_key"]: r for r in rows}


def test_routine_needs_three_user_backed_days_to_be_active():
    two = derive_observations([record(MON, ["Gym"]), record(TUE, ["Gym"])], [], cfg=CFG)
    assert [r["status"] for r in two if r["kind"] == "routine_weekday"] == ["candidate"]
    three = derive_observations([record(d, ["Gym"]) for d in (MON, TUE, WED)], [], cfg=CFG)
    row = next(r for r in three if r["kind"] == "routine_weekday")
    assert (row["status"], row["occurrences"], row["misses"]) == ("active", 3, 0)


def test_generated_blocks_count_only_once_the_user_edited_them():
    days = [MON, TUE, WED]
    assert [r for r in derive_observations([record(d, ["Gym"], origin="generated") for d in days], [], cfg=CFG)
            if r["kind"].startswith("routine")] == []
    kept = derive_observations([record(d, ["Gym"], origin="generated", edited=True) for d in days], [], cfg=CFG)
    assert any(r["kind"] == "routine_weekday" and r["status"] == "active" for r in kept)


def test_misses_question_then_drop_a_routine():
    base = [record(d, ["Gym"]) for d in (MON, TUE, WED)]
    status = lambda extra: next(r for r in derive_observations(base + extra, [], cfg=CFG) if r["kind"] == "routine_weekday")
    assert status([record(THU)])["status"] == "active"
    q = status([record(THU), record(FRI)])
    assert (q["status"], q["misses"]) == ("questioned", 2)
    d = status([record(THU), record(FRI), record(NEXT_MON)])
    assert (d["status"], d["misses"]) == ("dropped", 3)


def test_weekend_days_do_not_count_as_weekday_misses():
    base = [record(d, ["Gym"]) for d in (MON, TUE, WED)]
    sat, sun = record("2031-03-08"), record("2031-03-09")
    row = next(r for r in derive_observations(base + [sat, sun], [], cfg=CFG) if r["kind"] == "routine_weekday")
    assert row["misses"] == 0


def test_late_day_records_are_ignored():
    rows = derive_observations([record(d, ["Gym"], late=True) for d in (MON, TUE, WED)], [], cfg=CFG)
    assert rows == []


def test_focus_hour_counts_seconds_per_ist_hour_and_days():
    out = focus_hour_distribution([record(MON, actual=[("09:30", "10:30")])])
    assert out["seconds_by_hour"][9] == 1800 and out["seconds_by_hour"][10] == 1800
    assert out["days_by_hour"][9] == [MON]


def test_task_slips_count_only_later_moves():
    changes = [
        {"task_id": 1, "text": "Pay rent", "old_due": "2031-03-03T10:00:00+05:30", "new_due": "2031-03-04T10:00:00+05:30",
         "created_at": "2031-03-03T12:00:00+05:30"},
        {"task_id": 1, "text": "Pay rent", "old_due": "2031-03-04T10:00:00+05:30", "new_due": "2031-03-06T10:00:00+05:30",
         "created_at": "2031-03-04T12:00:00+05:30"},
        {"task_id": 2, "text": "Moved earlier", "old_due": "2031-03-05T10:00:00+05:30", "new_due": "2031-03-04T10:00:00+05:30",
         "created_at": "2031-03-03T12:00:00+05:30"},
        {"task_id": 3, "text": "Due removed", "old_due": "2031-03-05T10:00:00+05:30", "new_due": None, "created_at": "2031-03-03T12:00:00+05:30"},
    ]
    out = task_slip_counts(changes)
    assert [(r["task_id"], r["slips"], r["occurrences"]) for r in out] == [(1, 2, 2)]


def test_slipping_task_becomes_an_observation():
    changes = [{"task_id": 7, "text": "Pay rent", "old_due": "2031-03-03T10:00:00+05:30", "new_due": "2031-03-04T10:00:00+05:30",
                "created_at": "2031-03-03T12:00:00+05:30"}]
    rows = by_key(derive_observations([record(MON)], changes, cfg=CFG))
    assert rows["task_slips:7"]["status"] == "candidate"


def test_routine_summary_groups_titles_by_case():
    out = routine_summary([record(MON, ["Gym"]), record(TUE, ["gym"])])
    assert out["weekday"]["routines"][0]["occurrences"] == 2


def test_pattern_slot_is_a_window_not_an_exact_minute():
    at = lambda h, m: datetime(2031, 3, 3, h, m, 30, tzinfo=IST)
    assert due_slot(at(13, 0), set()) == ("2031-03-03", 13, 0)
    assert due_slot(at(13, 1), set()) == ("2031-03-03", 13, 0)       # a poll that drifted past :00 still runs
    assert due_slot(at(13, 1), {("2031-03-03", 13, 0)}) is None      # but only once
    assert due_slot(at(15, 30), set()) is None                       # laptop was off for the window: cycle skipped
    assert due_slot(at(21, 5), {("2031-03-03", 13, 0)}) == ("2031-03-03", 21, 0)
    assert due_slot(at(12, 59), set()) is None
