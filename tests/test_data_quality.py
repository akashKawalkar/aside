from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import review.daily as daily
from capture.heartbeat import Heartbeat, heartbeat_files
from config import DataQuality, Retention
from sessions import gaps as g
from storage.repo.retention import rules

IST = ZoneInfo("Asia/Kolkata")
CFG = DataQuality()
DAY = date(2026, 10, 5)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


# ---------- heartbeat ----------

def test_heartbeat_age_and_alive(tmp_path):
    hb = Heartbeat(tmp_path / "sub" / "hb.json")
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    assert hb.age(now) is None and not hb.alive(90, now)          # never beat
    hb.beat(now)
    assert hb.age(now + timedelta(seconds=30)) == 30 and hb.alive(90, now + timedelta(seconds=30))
    assert not hb.alive(90, now + timedelta(seconds=91))
    assert hb.age(now - timedelta(seconds=5)) == 0                # clock skew never gives a negative age
    assert not (tmp_path / "sub" / "hb.tmp").exists()             # atomic write leaves no temp file


def test_unreadable_heartbeat_counts_as_not_running(tmp_path):
    path = tmp_path / "hb.json"
    path.write_text("{not json", encoding="utf-8")
    assert Heartbeat(path).age() is None
    path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
    assert Heartbeat(path).age() is None


def test_collector_and_ingestor_use_separate_files(tmp_path):
    collector, ingestor = heartbeat_files(tmp_path)
    collector.beat()
    assert collector.alive(10) and not ingestor.alive(10)


# ---------- what the nightly worker owes ----------

def test_todays_snapshot_waits_for_the_snapshot_time():
    before = at(DAY, 23, 54)
    assert (DAY, False) not in daily.snapshots_due(before, set(), CFG)
    assert (DAY, False) in daily.snapshots_due(at(DAY, 23, 55), set(), CFG)


def test_missed_days_are_caught_up_as_late_and_only_once():
    now = at(DAY, 10)
    owed = daily.snapshots_due(now, set(), CFG)
    assert [d for d, _ in owed] == [DAY - timedelta(days=n) for n in range(7, 0, -1)]
    assert all(late for _, late in owed)
    have = {d for d, _ in owed}
    assert daily.snapshots_due(now, have, CFG) == []


def test_a_day_record_waits_for_the_sessionizer_then_flags_lateness():
    yesterday = DAY - timedelta(days=1)
    assert yesterday not in [d for d, _ in daily.records_due(at(DAY, 0, 19), set(), CFG)]    # 00:19: not settled yet
    assert daily.records_due(at(DAY, 0, 25), set(), CFG)[-1] == (yesterday, False)      # on time
    assert daily.records_due(at(DAY, 9), set(), CFG)[-1] == (yesterday, True)           # >2h after it was due
    assert (DAY, False) not in daily.records_due(at(DAY, 23, 59), set(), CFG)           # today is never owed


async def test_run_daily_once_writes_snapshots_and_records_and_is_idempotent(monkeypatch):
    snaps, records = {}, {}
    entry = {"id": 1, "title": "Gym", "start_at": at(DAY, 9), "end_at": at(DAY, 10), "origin": "generated", "edited_by_user": True}

    async def days_with_snapshot(pool, *, first, last): return {d for d in snaps if first <= d <= last}
    async def days_with_day_record(pool, *, first, last): return {d for d in records if first <= d <= last}
    async def list_schedule_range(pool, *, start, end): return [entry] if start.date() == DAY else []
    async def get_schedule_snapshot(pool, *, day): return snaps.get(day)
    async def save_schedule_snapshot(pool, *, day, entries, late): snaps.setdefault(day, entries); return True
    async def save_day_record(pool, *, day, data, late): records.setdefault(day, (data, late)); return True

    for name, fn in list(locals().items()):
        if name in vars(daily):
            monkeypatch.setattr(daily, name, fn)

    class Generator:
        async def day_data(self, day): return {"date": day.isoformat(), "when": at(day, 1)}   # a datetime: must be made JSON-safe

    now = at(DAY + timedelta(days=1), 0, 30)      # just after midnight: DAY is over
    written = await daily.run_daily_once(None, Generator(), CFG, now=now)
    assert DAY in written["snapshots"] and DAY in written["records"]
    assert snaps[DAY] == [{"id": 1, "title": "Gym", "start_at": at(DAY, 9).isoformat(), "end_at": at(DAY, 10).isoformat(),
                           "origin": "generated", "edited_by_user": True}]
    data, late = records[DAY]
    assert data["schedule"] == snaps[DAY] and data["facts"] == [] and data["review"]["date"] == DAY.isoformat()
    assert isinstance(data["review"]["when"], str)
    assert late is False

    assert await daily.run_daily_once(None, Generator(), CFG, now=now) == {"snapshots": [], "records": []}


# ---------- gaps ----------

def session(day, start, end, kind="app"):
    return {"started_at": at(day, *start), "ended_at": at(day, *end), "kind": kind}


def picture(sessions, *, monitoring=(), heartbeats=(), now=None, first=None):
    return g.month_picture(2026, 10, sessions=sessions, monitoring=list(monitoring), heartbeats=list(heartbeats),
                           now=now or at(date(2026, 10, 6), 12), cfg=CFG, first_data_at=first or at(DAY, 7))


def test_interval_helpers():
    a, b, c = at(DAY, 9), at(DAY, 10), at(DAY, 11)
    assert g.merge([(b, c), (a, b)]) == [(a, c)]
    assert g.complement([(a, b)], (at(DAY, 8), c)) == [(at(DAY, 8), a), (b, c)]
    assert g.clip([(at(DAY, 6), at(DAY, 8))], (a, c)) == []


def test_gap_found_inside_waking_hours_idle_counts_as_tracked_and_short_gaps_are_ignored():
    sessions = [session(DAY, (7, 0), (10, 0)), session(DAY, (10, 5), (11, 0)),   # 5 min: below the 15-minute floor
                session(DAY, (11, 0), (12, 0), kind="idle"), session(DAY, (14, 0), (23, 0))]
    out = picture(sessions)
    day = out["days"][4]
    assert day["date"] == "2026-10-05" and day["tracked_seconds"] == 13 * 3600 + 55 * 60   # 7-10, 10:05-12 (idle joins), 14-23
    on_the_5th = [x for x in out["gaps"] if x["start"].startswith("2026-10-05")]
    assert [(x["start"][11:16], x["end"][11:16], x["minutes"]) for x in on_the_5th] == [("12:00", "14:00", 120)]
    assert on_the_5th[0]["reason"] == g.UNEXPLAINED   # no heartbeats exist yet


def test_nothing_before_tracking_began_or_in_the_future_is_a_gap():
    out = picture([session(DAY, (9, 0), (23, 0))], first=at(DAY, 9), now=at(date(2026, 10, 6), 8))
    by_day = {d["date"]: d for d in out["days"]}
    assert by_day["2026-10-05"]["gap_count"] == 0       # 07:00-09:00 predates the first session
    assert by_day["2026-10-06"]["gap_count"] == 1       # 07:00-08:00 so far, "now" is 08:00
    assert by_day["2026-10-07"]["in_future"] and by_day["2026-10-07"]["gap_count"] == 0
    assert by_day["2026-10-01"]["gap_count"] == 0       # before the first data


def test_pause_intervals():
    events = [{"enabled": False, "at": at(DAY, 10)}, {"enabled": True, "at": at(DAY, 11)}, {"enabled": False, "at": at(DAY, 13)}]
    assert g.pause_intervals(events, at(DAY, 15)) == [(at(DAY, 10), at(DAY, 11)), (at(DAY, 13), at(DAY, 15))]
    resumed_first = [{"enabled": True, "at": at(DAY, 9)}]
    assert g.pause_intervals(resumed_first, at(DAY, 15)) == []


def beats(day, start_hm, end_hm, *, collector=True, ingestor=True):
    out, t = [], at(day, *start_hm)
    while t < at(day, *end_hm):
        out.append({"ts": t, "collector_ok": collector, "ingestor_ok": ingestor})
        t += timedelta(seconds=60)
    return out


@pytest.mark.parametrize("hb, expected", [
    ([], g.UNEXPLAINED),                                                         # nothing recorded yet
    (beats(DAY, (7, 0), (8, 0)) + beats(DAY, (16, 0), (17, 0)), g.MACHINE_OFF),  # silent during the gap
    (beats(DAY, (7, 0), (17, 0), collector=False), g.COLLECTOR_DOWN),
    (beats(DAY, (7, 0), (17, 0), ingestor=False), g.INGESTOR_DOWN),
    (beats(DAY, (7, 0), (17, 0)), g.UNEXPLAINED),                                # everything was up, still no data
])
def test_gap_reasons_from_heartbeats(hb, expected):
    out = picture([session(DAY, (7, 0), (12, 0)), session(DAY, (14, 0), (23, 0))], heartbeats=hb)
    assert out["gaps"][0]["reason"] == expected


def test_a_pause_beats_every_other_reason():
    pause = [{"enabled": False, "at": at(DAY, 11, 55)}, {"enabled": True, "at": at(DAY, 14, 5)}]
    out = picture([session(DAY, (7, 0), (12, 0)), session(DAY, (14, 0), (23, 0))], monitoring=pause,
                  heartbeats=beats(DAY, (7, 0), (17, 0), collector=False))
    assert out["gaps"][0]["reason"] == g.PAUSED


def test_heatmap_tracked_seconds_include_time_outside_waking_hours():
    out = picture([session(DAY, (5, 0), (6, 30))])
    assert out["days"][4]["tracked_seconds"] == 90 * 60


# ---------- retention ----------

def test_retention_rules_follow_config_and_never_name_derived_tables():
    r = Retention(raw_events_days=30, past_schedule_days=60)
    by_name = {x.name: x for x in rules(r)}
    assert by_name["raw events"].days == 30 and by_name["past schedule entries"].days == 60
    now = datetime(2026, 10, 6, tzinfo=IST)
    assert by_name["raw events"].cutoff(now) == now - timedelta(days=30)
    touched = {x.table for x in rules(r)}
    assert touched.isdisjoint({"sessions", "day_record", "schedule_snapshot", "schedule_log", "task_due_log", "monitoring_log"})
