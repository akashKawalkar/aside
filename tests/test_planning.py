"""The planning prompt and the checks on its answer. Pure code."""
from __future__ import annotations

import copy
from datetime import date

from practiced.plancheck import check, to_blocks
from practiced.planning import (
    EXAMPLE_BUDGET, EXAMPLE_FIXED, EXAMPLE_OUTPUT, SYSTEM, PlanInputs, build_messages, dial_numbers, grid_lines, render_user,
)

DAY = date(2026, 11, 16)
TASKS = [{"id": 7, "text": "Renew passport form", "due": "2026-11-16", "slip_count": 3, "source": "Google Tasks"}]


def inp(**kw):
    kw.setdefault("budget_slots", EXAMPLE_BUDGET)
    return PlanInputs(day=DAY, fixed=list(EXAMPLE_FIXED), tasks=list(TASKS), **kw)


def test_the_worked_example_passes_the_checker():
    assert check(EXAMPLE_OUTPUT, inp()) == []


def test_the_prompt_holds_the_rules_the_example_and_the_grid():
    msgs = build_messages(inp(preferences=[("goals", "To get a job as AI engineer.")]))
    assert "Never use a slot marked FIXED" in msgs[0].content and "EXAMPLE" in msgs[0].content
    u = msgs[1].content
    assert " 0 05:00 FREE" in u and " 8 09:00 FIXED: Dentist" in u and "47 04:30 (after midnight) FREE" in u and "38 00:00 (after midnight)" in u
    assert "[task 7] Renew passport form | due 2026-11-16 | slipped 3 times" in u and "[goals] To get a job" in u and "IDEAL_WEIGHT: 0.3" in u and "DEEP WORK BUDGET" in u


def test_grid_has_forty_eight_rows():
    assert len(grid_lines(DAY, [])) == 48


def bad(mut, **kw):
    out = copy.deepcopy(EXAMPLE_OUTPUT)
    mut(out)
    return check(out, inp(**kw))


def test_a_missing_free_slot_is_named():
    assert any("no block: 32-33" in p for p in bad(lambda o: o["plan"].pop(16)))


def test_using_a_fixed_slot_or_overlapping_is_caught():
    p = bad(lambda o: o["plan"][3].update(start_slot=9))
    assert any("FIXED" in x for x in p)
    p = bad(lambda o: o["plan"][4].update(start_slot=11))
    assert any("overlaps" in x for x in p)


def test_screen_runs_and_repeated_modes_are_caught():
    def no_break(o):                      # the 22:00 break becomes more build work, right between two build blocks
        o["plan"][4].update(kind="work", mode="build", depth="shallow", title="More coding")
        o["plan"][4].pop("minutes")
    p = bad(no_break)
    assert any("same screen mode" in x for x in p) and any("in a row" in x for x in p)


def test_breaks_come_from_the_menu_and_have_minutes():
    p = bad(lambda o: o["plan"][1].update(title="Scroll Instagram"))
    assert any("menu" in x for x in p)
    p = bad(lambda o: o["plan"][1].update(minutes=45))
    assert any("between 5 and 30" in x for x in p)


def test_a_missing_meditation_workout_or_outdoor_block_is_caught():
    def drop(o):
        o["plan"][:] = [b for b in o["plan"] if "medit" not in b["title"].lower()]
    assert any("meditation block" in x for x in bad(drop))
    def indoor(o):
        for b in o["plan"]:
            if b["mode"] == "outdoor":
                b["mode"] = "rest"
    assert any("outdoor" in x for x in bad(indoor))


def test_meditation_too_soon_after_a_meal_is_caught():
    def move(o):
        o["plan"][0]["start_slot"] = 4
        o["plan"].insert(1, {"start_slot": 5, "slots": 1, "kind": "anchor", "title": "Breakfast", "mode": "rest", "why": "x"})
        o["plan"].pop(2)         # the walk
    assert any("4 hours" in x for x in bad(move))


def test_tasks_due_must_be_placed_once():
    assert any("is due and has no block" in x for x in bad(lambda o: o["plan"][3].pop("task_id")))
    assert any("not an open task" in x for x in bad(lambda o: o["plan"][3].update(task_id=99)))


def test_checklist_false_needs_a_fix_and_all_keys_are_required():
    assert any("no \"fix\"" in x for x in bad(lambda o: o["checklist"]["every_free_slot_filled"].update(ok=False)))
    assert any("checklist must have" in x for x in bad(lambda o: o["checklist"].pop("at_least_one_outdoor_block")))


def test_missing_keys_and_non_objects():
    assert check([], inp()) == ["The answer must be one JSON object."]
    assert any('"anchors"' in x for x in bad(lambda o: o.pop("anchors")))


def test_replanning_needs_feedback_constraints():
    assert any("feedback_constraints" in x for x in check(EXAMPLE_OUTPUT, inp(current_plan=[{"x": 1}], feedback="less study")))


def test_slots_become_clock_times_and_breaks_end_after_their_own_minutes():
    rows = to_blocks(EXAMPLE_OUTPUT, inp())
    first, walk = rows[0], rows[1]
    assert first["start"].strftime("%H:%M") == "07:00" and first["end"].strftime("%H:%M") == "07:30"
    assert walk["start"].strftime("%H:%M") == "07:30" and walk["end"].strftime("%H:%M") == "07:50"


def test_dial_numbers_pull_the_real_toward_the_ideal():
    assert dial_numbers(176, 36, 0.3) == (8, 2)        # 5.9 real slots -> 5.9 + 0.3 x 6.1 = 7.7; 1.2 -> 1.2 + 0.3 x 1.8 = 1.7
    assert dial_numbers(176, 36, 0.0) == (6, 1) and dial_numbers(176, 36, 1.0) == (12, 3)


def test_free_slots_and_demand_must_add_up():
    assert any("free_slots_left must be 23" in x for x in bad(lambda o: o["anchors"].update(free_slots_left=24)))
    assert any("add up to 15" in x for x in bad(lambda o: o["demand"].pop()))


def test_window_clock_times_must_match_the_slots():
    assert any("are 14:00-17:00 in clock time" in x for x in bad(lambda o: o["windows"][1].update(clock="20:00-23:00")))
    assert any("Every window needs" in x for x in bad(lambda o: o["windows"][0].update(rating="great")))


def test_deep_work_must_match_the_budget_and_stay_out_of_risky_windows():
    assert any("DEEP WORK BUDGET is 3" in x for x in check(EXAMPLE_OUTPUT, inp(budget_slots=3)))
    def into_risky(o):
        o["windows"][2].update(slots="18-23", clock="14:00-17:00")
    assert any("rated risky" in x for x in bad(into_risky))


def test_work_blocks_need_a_depth():
    assert any('"depth"' in x for x in bad(lambda o: o["plan"][3].pop("depth")))


def test_deep_work_outside_every_rated_window_is_caught():
    def move(o):
        o["windows"][0].update(slots="10-12", clock="10:00-11:30")       # the 23-25 deep block now sits outside the strong window
    assert any("not inside one window" in x for x in bad(move))


def test_a_five_minute_routine_break_is_allowed():
    assert check(EXAMPLE_OUTPUT, inp()) == [] and not any("between 5 and 30" in x for x in bad(lambda o: o["plan"][17].update(minutes=5)))


def test_midnight_may_be_written_as_the_end_of_a_window_and_raw_newlines_in_strings_are_read():
    from practiced.llmcall import parse_json
    assert not any("clock time" in x for x in bad(lambda o: o["windows"][2].update(slots="32-37", clock="21:00-00:00")))
    raw = '{"a": "one' + chr(10) + 'two"}'
    assert parse_json(raw) == {"a": "one" + chr(10) + "two"}


def test_the_wake_slot_must_be_near_the_expected_one():
    assert check(EXAMPLE_OUTPUT, inp(expected_wake_slot=5)) == []                       # the example wakes at slot 4
    assert any("expected to wake at slot 12" in x for x in check(EXAMPLE_OUTPUT, inp(expected_wake_slot=12)))


def test_last_night_is_shown_when_given_and_slots_after_midnight_become_the_next_calendar_day():
    from practiced.planning import slot_time
    assert "LAST NIGHT" in render_user(inp(last_night="bedtime about 03:40"))
    assert (slot_time(0), slot_time(10), slot_time(38), slot_time(47)) == ("05:00", "10:00", "00:00", "04:30")
    late = to_blocks({"plan": [{"start_slot": 40, "slots": 2, "kind": "work", "title": "x", "mode": "build", "why": "y"}]}, inp())[0]
    assert late["start"].day == 17 and late["start"].strftime("%H:%M") == "01:00"
