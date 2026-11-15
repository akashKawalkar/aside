"""Checks on the planning call's answer, and the conversion from slots to clock times. Pure code.

`check` returns what is wrong as plain sentences, which are shown back to the model on a retry (at most two retries, then the
night job fails; there is no template fallback).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from practiced.planning import (
    BREAK_MENU, BUDGET_TOLERANCE, DEPTHS, WAKE_TOLERANCE, KINDS, MAX_SCREEN_RUN, MEDITATION_GAP_SLOTS, MODES, SCREEN_MODES, SLOTS, PlanInputs,
    slot_range_clock,
)

IST = ZoneInfo("Asia/Kolkata")
KEYS = ("anchors", "demand", "demand_vs_free", "windows", "placement_notes", "checklist", "plan")
CHECKLIST = (
    "every_free_slot_filled", "meditation_gap_respected", "all_tasks_due_tomorrow_placed", "deep_work_matches_dial",
    "carryover_accounted_for", "long_term_goal_served", "no_screen_run_over_3_slots", "no_same_mode_back_to_back",
    "at_least_one_outdoor_block",
)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check(out: Any, inp: PlanInputs) -> list[str]:
    if not isinstance(out, dict):
        return ["The answer must be one JSON object."]
    bad: list[str] = []
    for k in KEYS:
        if k not in out:
            bad.append(f'The key "{k}" is missing.')
    if inp.current_plan and "feedback_constraints" not in out:
        bad.append('The key "feedback_constraints" is missing (it comes first when re-planning).')

    anchors = out.get("anchors") if isinstance(out.get("anchors"), dict) else {}
    wake, sleep = anchors.get("wake_slot"), anchors.get("sleep_slot")
    if not (_is_int(wake) and _is_int(sleep) and 0 <= wake < sleep <= SLOTS):
        bad.append("anchors.wake_slot and anchors.sleep_slot must be whole slot numbers with wake_slot below sleep_slot (sleep_slot may be 48).")
        wake, sleep = 0, SLOTS

    if inp.expected_wake_slot is not None and _is_int(wake) and abs(wake - inp.expected_wake_slot) > WAKE_TOLERANCE:
        bad.append(f"anchors.wake_slot is {wake}, but the person is expected to wake at slot {inp.expected_wake_slot} (LAST NIGHT); stay within {WAKE_TOLERANCE} slots of it.")

    cl = out.get("checklist")
    if not isinstance(cl, dict) or any(k not in cl or not isinstance(cl[k], dict) or not isinstance(cl[k].get("ok"), bool) for k in CHECKLIST):
        bad.append("checklist must have every one of these keys, each {\"ok\": true or false, \"fix\": \"...\"}: " + ", ".join(CHECKLIST) + ".")
    else:
        bad += [f'checklist.{k} is false but has no "fix".' for k in CHECKLIST if not cl[k]["ok"] and not str(cl[k].get("fix", "")).strip()]

    plan = out.get("plan")
    if not isinstance(plan, list) or not plan:
        return bad + ['"plan" must be a non-empty list of blocks.']

    fixed_slots = {s for f in inp.fixed for s in range(f.start, f.end)}
    owner: dict[int, int] = {}
    blocks = []
    for i, b in enumerate(plan, 1):
        if not isinstance(b, dict) or not _is_int(b.get("start_slot")) or not _is_int(b.get("slots")) or b["slots"] < 1:
            bad.append(f"plan block {i} needs whole numbers start_slot and slots (slots at least 1).")
            continue
        if not isinstance(b.get("title"), str) or not b["title"].strip() or not isinstance(b.get("why"), str) or not b["why"].strip():
            bad.append(f'plan block {i} needs a non-empty "title" and "why".')
            continue
        if b.get("kind") not in KINDS or b.get("mode") not in MODES:
            bad.append(f'plan block {i} ("{b["title"]}") needs kind in {list(KINDS)} and mode in {list(MODES)}.')
            continue
        if b["kind"] == "work" and b.get("depth") not in DEPTHS:
            bad.append(f'work block {i} ("{b["title"]}") needs "depth": "deep" or "shallow".')
            continue
        first, last = b["start_slot"], b["start_slot"] + b["slots"] - 1
        if first < 0 or last >= SLOTS:
            bad.append(f'plan block {i} ("{b["title"]}") runs outside slots 0 to {SLOTS - 1}.')
            continue
        if b["kind"] == "break":
            if not _is_int(b.get("minutes")) or not 5 <= b["minutes"] <= 30:
                bad.append(f'break "{b["title"]}" needs "minutes" between 5 and 30.')
            if not any(item in " ".join(b["title"].lower().split()) for item in BREAK_MENU):
                bad.append(f'break "{b["title"]}" must be picked from the menu: {", ".join(BREAK_MENU)}.')
            if b["slots"] > 2:
                bad.append(f'break "{b["title"]}" is longer than two slots.')
        for s in range(first, last + 1):
            if s in fixed_slots:
                bad.append(f'plan block {i} ("{b["title"]}") uses slot {s}, which is FIXED.')
                break
        for s in range(first, last + 1):
            if s in owner:
                bad.append(f'plan block {i} ("{b["title"]}") overlaps block {owner[s]} at slot {s}.')
                break
            owner[s] = i
        blocks.append(b)

    missing = [s for s in range(wake, sleep) if s not in fixed_slots and s not in owner]
    if missing:
        bad.append("These free slots have no block: " + _ranges(missing) + ".")

    ordered = sorted(blocks, key=lambda b: b["start_slot"])
    run, prev = 0, None
    for b in ordered:
        screen = b["mode"] in SCREEN_MODES
        touching = prev is not None and prev["start_slot"] + prev["slots"] == b["start_slot"]
        if screen and touching and prev["mode"] == b["mode"]:
            bad.append(f'"{prev["title"]}" and "{b["title"]}" are the same screen mode ({b["mode"]}) back to back.')
        run = (run if touching else 0) + b["slots"] if screen else 0
        if screen and run > MAX_SCREEN_RUN:
            bad.append(f'Screen work reaches {run} slots in a row at "{b["title"]}"; the limit is {MAX_SCREEN_RUN}.')
        prev = b

    meditation = next((b for b in ordered if b["kind"] == "anchor" and "medit" in b["title"].lower()), None)
    if meditation is None:
        bad.append('The plan needs a meditation block (kind "anchor", title containing "Meditation").')
    if not any(b["kind"] == "anchor" and any(w in b["title"].lower() for w in ("workout", "exercise", "yoga")) for b in ordered):
        bad.append('The plan needs a workout block (kind "anchor", title containing "Workout").')
    meals = [b for b in ordered if b["kind"] == "anchor" and any(w in b["title"].lower() for w in ("lunch", "dinner", "breakfast", "meal"))]
    if meditation is not None:
        before = [m for m in meals if m["start_slot"] + m["slots"] <= meditation["start_slot"]]
        if before and meditation["start_slot"] - (before[-1]["start_slot"] + before[-1]["slots"]) < MEDITATION_GAP_SLOTS:
            bad.append(f'Meditation starts less than 4 hours after "{before[-1]["title"]}" ended.')
    if not any(b["mode"] == "outdoor" for b in ordered):
        bad.append("The plan needs at least one outdoor block.")

    bad += _check_numbers(out, inp, ordered, wake, sleep, fixed_slots)

    known = {t["id"] for t in inp.tasks}
    used = [b.get("task_id") for b in ordered if b.get("task_id") is not None]
    bad += [f"task_id {t} is not an open task." for t in used if t not in known]
    if len(used) != len(set(used)):
        bad.append("A task is on more than one block; use one block per task.")
    today_due = [t["id"] for t in inp.tasks if t.get("due") and str(t["due"])[:10] <= inp.day.isoformat()]
    bad += [f"[task {t}] is due and has no block." for t in today_due if t not in used]
    return bad


def _ranges(slots: list[int]) -> str:
    out, start, prev = [], slots[0], slots[0]
    for s in slots[1:] + [None]:
        if s is not None and s == prev + 1:
            prev = s
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if s is not None:
            start = prev = s
    return ", ".join(out)


def to_blocks(out: dict, inp: PlanInputs) -> list[dict]:
    """The plan as clock times, ready for the calendar. A break ends after its own minutes, the rest of its slot is transition."""
    day0 = datetime.combine(inp.day, time(5, 0), IST)          # the grid starts at 05:00
    rows = []
    for b in sorted(out["plan"], key=lambda b: b["start_slot"]):
        start = day0 + timedelta(minutes=30 * b["start_slot"])
        minutes = b["minutes"] if b["kind"] == "break" else 30 * b["slots"]
        rows.append({"title": b["title"], "start": start, "end": start + timedelta(minutes=minutes), "why": b["why"],
                     "mode": b["mode"], "kind": b["kind"], "task_id": b.get("task_id")})
    return rows


def _window_slots(w: Any) -> tuple[int, int] | None:
    try:
        a, b = str(w["slots"]).split("-")
        a, b = int(a), int(b)
    except (KeyError, ValueError, TypeError):
        return None
    return (a, b) if 0 <= a <= b < SLOTS else None


def _check_numbers(out: dict, inp: PlanInputs, ordered: list[dict], wake: int, sleep: int, fixed_slots: set[int]) -> list[str]:
    """The arithmetic the model must get right: free slots, demand total, window clock times, the deep-work budget."""
    bad: list[str] = []
    anchors = out.get("anchors") if isinstance(out.get("anchors"), dict) else {}
    anchor_slots = sum(b["slots"] for b in ordered if b["kind"] == "anchor" and wake <= b["start_slot"] < sleep)
    left = sleep - wake - len([s for s in fixed_slots if wake <= s < sleep]) - anchor_slots
    if anchors.get("free_slots_left") != left:
        bad.append(f"anchors.free_slots_left must be {left} (slots from wake_slot to sleep_slot, minus FIXED slots, minus the anchor blocks), not {anchors.get('free_slots_left')}.")

    demand = out.get("demand")
    if not isinstance(demand, list) or not demand or not all(isinstance(d, dict) and _is_int(d.get("slots")) and d["slots"] >= 0 for d in demand):
        bad.append('"demand" must be a list of items, each with whole-number "slots".')
    elif sum(d["slots"] for d in demand) != left:
        bad.append(f"The demand slots add up to {sum(d['slots'] for d in demand)} but must add up to exactly {left} (free_slots_left). "
                   "Add goal work, shallow work or active breaks to the demand list, or cut items.")

    risky: list[tuple[int, int]] = []
    usable: list[tuple[int, int]] = []
    windows = out.get("windows")
    if not isinstance(windows, list) or not windows:
        bad.append('"windows" must be a non-empty list.')
    else:
        for w in windows:
            rng = _window_slots(w)
            if rng is None or not isinstance(w, dict) or w.get("rating") not in ("strong", "weak", "risky"):
                bad.append(f'Every window needs "slots" like "20-25" (inside 0-47) and "rating" strong, weak or risky: {w}.')
                continue
            if w.get("clock") != slot_range_clock(*rng):
                bad.append(f'Window slots {w["slots"]} are {slot_range_clock(*rng)} in clock time, not "{w.get("clock")}".')
            if w["rating"] == "risky":
                risky.append(rng)
            else:
                usable.append(rng)

    deep = [b for b in ordered if b["kind"] == "work" and b.get("depth") == "deep"]
    if inp.budget_slots:
        total = sum(b["slots"] for b in deep)
        lo, hi = max(inp.budget_slots - BUDGET_TOLERANCE, 0), inp.budget_slots + BUDGET_TOLERANCE
        if not lo <= total <= hi:
            bad.append(f"Deep work totals {total} slots; the DEEP WORK BUDGET is {inp.budget_slots} ({lo} to {hi} is fine). "
                       "Mark lighter work \"shallow\" or add/cut deep work.")
    for b in deep:
        first, last = b["start_slot"], b["start_slot"] + b["slots"] - 1
        if any(first <= r1 and last >= r0 for r0, r1 in risky):
            bad.append(f'Deep work "{b["title"]}" is in a window you rated risky; move it to a strong or weak window, or mark it shallow.')
        elif not any(r0 <= first and last <= r1 for r0, r1 in usable):
            bad.append(f'Deep work "{b["title"]}" (slots {first}-{last}) is not inside one window you rated strong or weak; add or widen a window, or move it.')
    return bad
