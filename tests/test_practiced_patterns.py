"""Phone answers and per-day stats in the practiced schedule, then patterns and real durations over many days. Pure code."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from digital.builder import IST, Ev, build_digital_schedule
from digital.seed import SEED_RULES
from practiced.builder import Plan, Tag, build_practiced_schedule
from practiced.digest import render
from practiced.durations import real_durations, render_durations
from practiced.patterns import compute_patterns, render_patterns

D = date(2026, 11, 12)


def t(h, m=0, d=0):
    return datetime(2026, 11, 12 + d, h, m, tzinfo=IST)


def code(s, e):
    return Ev("laptop", "app_focus", s, e, app="Code.exe", title="a.py - proj - Visual Studio Code")


def reel(s, e):
    return Ev("android", "phone_app_focus", s, e, app="com.instagram.android")


def day_of(events, plans=(), tags=None, **kw):
    return build_practiced_schedule(build_digital_schedule(D, events, SEED_RULES), list(plans), tags or {}, **kw)


# --- answers and stats in one day -------------------------------------------------------------------------------------------

def test_a_distraction_reason_attaches_to_the_block_it_was_given_in_or_to_outside():
    ans = [{"kind": "distraction", "answered_at": t(10, 30), "payload": {"reason": "break", "minutes": 12, "apps": "Instagram"}},
           {"kind": "distraction", "answered_at": t(20), "payload": {"reason": "avoiding", "text": "dreading the email"}}]
    r = day_of([code(t(10), t(11))], [Plan(1, "Work", t(10), t(12))], {"Work": Tag(("coding",))}, answers=ans)
    assert r["plan"][0]["reasons"] == [{"at": "10:30", "reason": "break", "minutes": 12, "apps": "Instagram"}]
    assert r["outside_reasons"] == [{"at": "20:00", "reason": "avoiding", "text": "dreading the email"}]


def test_the_evening_sheet_speaks_for_blocks_the_devices_cannot_see_but_does_not_change_the_verdict():
    ans = [{"kind": "evening", "answered_at": t(21), "payload": {"workout": "yes", "naps": []}}]
    tags = {"Morning Workout": Tag((), False), "Deep work": Tag(("coding",))}
    r = day_of([code(t(10), t(11))], [Plan(1, "Morning Workout", t(7), t(8)), Plan(2, "Deep work", t(10), t(12))], tags, answers=ans)
    w = r["plan"][0]
    assert (w["verdict"], w["self_report"]) == ("NO SIGNAL", "yes") and "self_report" not in r["plan"][1]
    assert "the person said: yes" in render(r)


def test_an_unanswered_sheet_leaves_no_claim():
    r = day_of([], [Plan(1, "Morning Workout", t(7), t(8))], {"Morning Workout": Tag((), False)}, answers=[])
    assert "self_report" not in r["plan"][0] and r["evening"] is None and r["sleep"] is None


def test_sleep_answer_and_context_are_kept():
    ans = [{"kind": "sleep", "answered_at": t(10), "payload": {"sleep_start": t(2, 30).isoformat(), "wake": t(9, 40).isoformat(), "minutes": 430, "edited": True}}]
    r = day_of([], answers=ans, context="travel")
    assert r["sleep"] == {"start": "02:30", "wake": "09:40", "minutes": 430, "edited": True} and r["context"] == "travel"
    assert "the person said this day was: travel" in render(r)


def test_stats_hours_runs_and_phone_over_productive_time():
    r = day_of([code(t(10), t(11)), code(t(11), t(11, 30)), code(t(14), t(14, 20)), reel(t(10, 20), t(10, 50))])
    s = r["stats"]
    assert s["by_hour"]["10"] == {"active": 60.0, "productive": 60.0}
    assert s["productive_runs"] == [90, 20] and s["productive_laptop_min"] == 110
    assert s["phone_over_productive_min"] == 30


# --- patterns over many days ------------------------------------------------------------------------------------------------

def block(planned, on_plan, *, laptop=None, verdict=None, expected=("coding",)):
    pct = round(100 * on_plan / planned)
    return {"title": "x", "planned_min": planned, "on_plan_min": on_plan, "on_plan_pct": pct, "expected": list(expected),
            "verdict": verdict or ("DONE" if pct >= 60 else "PARTIAL" if pct >= 25 else "OFF-PLAN"),
            "laptop_data_min": planned if laptop is None else laptop}


def day(n, blocks, *, context="normal", prod=100, phone=60, runs=(30,), hour=None, sleep=None, over=20):
    d = {"day": (date(2026, 11, 1) + timedelta(days=n)).isoformat(), "context": context, "plan": blocks, "sleep": sleep,
         "totals": {"laptop": {}, "phone": {"social": phone}},
         "stats": {"by_hour": hour or {}, "productive_runs": list(runs), "productive_laptop_min": prod, "phone_over_productive_min": over}}
    return d


TODAY = date(2026, 11, 20)


def test_adherence_by_length_uses_only_blocks_with_the_laptop_on():
    days = [day(0, [block(60, 48), block(60, 12), block(180, 20), block(180, 0, laptop=10), block(30, 0, verdict="NO SIGNAL")])]
    a = compute_patterns(days, TODAY)["adherence_by_length"]
    rows = {r["length"]: r for r in a["rows"]}
    assert rows["up to 90m"]["n"] == 2 and rows["up to 90m"]["median_on_plan_pct"] == 50      # median of 80 and 20
    assert rows["over 150m"]["n"] == 1 and a["left_out"] == {"laptop_off": 1, "no_signal": 1, "untagged": 0}


def test_travel_and_sick_days_are_not_learned_from():
    days = [day(0, [block(60, 60)]), day(1, [block(60, 0)], context="travel"), day(2, [block(60, 0)], context="sick")]
    p = compute_patterns(days, TODAY)
    assert p["days_learned_from"] == 1 and p["adherence_by_length"]["rows"][0]["median_on_plan_pct"] == 100
    assert p["trend"]["not_learned_from"] == ["2026-11-02", "2026-11-03"]


def test_best_and_worst_hours_need_enough_support():
    hour = {"10": {"productive": 40, "active": 50}, "12": {"productive": 30, "active": 50}, "15": {"productive": 20, "active": 50},
            "21": {"productive": 10, "active": 50}, "03": {"productive": 5, "active": 8}}
    out = compute_patterns([day(n, [], hour=hour) for n in range(4)], TODAY)["hours"]
    assert [r["hour"] for r in out["best"]] == ["10", "12", "15"] and out["best"][0]["productive_share"] == 80      # hour 03 has too little data
    assert [r["hour"] for r in out["worst"]] == ["21"]


def test_run_lengths_and_phone_co_use():
    days = [day(0, [], runs=(10, 20, 90), prod=120, over=30), day(1, [], runs=(40,), prod=100, over=10), day(2, [], runs=(), prod=0, over=0)]
    p = compute_patterns(days, TODAY)
    assert p["run_lengths"]["n"] == 4 and p["run_lengths"]["share_60_plus"] == 25 and p["run_lengths"]["max"] == 90
    assert p["phone_co_use"] == {"n_days": 2, "median_share_pct": 18, "median_minutes_per_day": 20}


def test_trend_compares_the_last_fourteen_days_with_the_fourteen_before():
    days = [day(n, [], prod=400) for n in range(0, 5)] + [day(n, [], prod=100) for n in range(5, 19)]      # Nov 1-5, then Nov 6-19
    tr = compute_patterns(days, date(2026, 11, 20))["trend"]
    assert tr["recent"]["days"] == 14 and tr["before"]["days"] == 5 and tr["recent"]["productive_laptop_min"] == 100 and tr["before"]["productive_laptop_min"] == 400


def test_data_health_flags_a_mostly_off_laptop():
    days = [day(n, [block(60, 0, laptop=0), block(60, 30)]) for n in range(10, 15)]
    h = compute_patterns(days, TODAY)["data_health"]
    assert h["laptop_off_pct"] == 50 and h["flag"] is True


def test_sleep_window_handles_bedtimes_either_side_of_midnight():
    s = lambda a, b, m: {"start": a, "wake": b, "minutes": m, "edited": False}
    days = [day(0, [], sleep=s("23:30", "07:30", 480)), day(1, [], sleep=s("01:30", "09:30", 480)), day(2, [], sleep=s("00:30", "08:30", 480))]
    assert compute_patterns(days, TODAY)["sleep"] == {"n": 3, "inferred": False, "median_bedtime": "00:30", "median_wake": "08:30", "median_minutes": 480}


def test_rendered_patterns_show_counts_and_call_thin_evidence_thin():
    text = render_patterns(compute_patterns([day(0, [block(60, 48), block(180, 20)])], TODAY))
    assert "n=1, thin" in text and "Learned from 1 normal days of 1" in text and "no data" in text


def test_nothing_is_learned_from_today_or_the_future():
    p = compute_patterns([day(0, [block(60, 60)]), day(19, [block(60, 0)])], TODAY)       # day 19 is 2026-11-20 = today
    assert p["days_seen"] == 1


# --- real durations -------------------------------------------------------------------------------------------------------

def test_real_durations_are_per_block_type_from_blocks_with_the_laptop_on():
    days = [day(0, [block(120, 30), block(120, 60), block(60, 50, expected=("study",)), block(120, 0, laptop=0)])]
    rows = {r["type"]: r for r in real_durations(days)}
    assert rows["coding"] == {"type": "coding", "n": 2, "planned_median_min": 120, "actual_median_min": 45, "actual_over_planned_pct": 38}
    assert rows["study"]["actual_over_planned_pct"] == 83
    assert "coding: planned 120m, on plan 45m (38%), n=2, thin" in render_durations(list(rows.values()))


def test_no_durations_no_line():
    assert render_durations(real_durations([])) == ""


def test_recent_work_is_described_plainly_and_without_app_prefixes():
    from practiced.patterns import recent_work
    days = [{"day": "2026-11-10", "stats": {"work": {"VS Code: personal-agent": 120, "ChatGPT": 60, "ChatGPT: Interview prep": 90, "PDF: LLM Serving": 20}}}]
    got = recent_work(days, date(2026, 11, 15))
    assert got[0] == "coding project personal-agent: 120m" and got[1].startswith("topic worked on in ChatGPT: Interview prep")
    assert all(not g.startswith("ChatGPT:") for g in got) and len(got) == 3
