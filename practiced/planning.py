"""The planning prompt: everything the planning call reads, laid out as text (planning_design.md sections 3 to 5).

Code does the arithmetic and the joins (slot grid, free slots, patterns, durations, the dial numbers); the model only judges and
writes. The model answers in slot numbers, never clock times; code converts them and checks the plan (practiced/plancheck.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from llm.client import Message

PROMPT_VERSION = 6
SLOTS = 48                                     # half-hour slots; the day runs 05:00 to 05:00, so slot 0 = 05:00 and slot 38 = 00:00
DAY_START_MIN = 5 * 60                         # the logical day begins at 05:00 IST
MODES = ("study", "build", "physical", "outdoor", "rest", "voice")
SCREEN_MODES = ("study", "build")
KINDS = ("anchor", "work", "break")
DEPTHS = ("deep", "shallow")
MAX_SCREEN_RUN = 3                             # slots in a row of screen work before an off-screen break
MEDITATION_GAP_SLOTS = 8                       # evening meditation: 4 hours after lunch
LATEST_DINNER_SLOT = 33                        # dinner may move later for meditation, but starts by 21:30
BREAK_MENU = ("sing songs", "short meditation", "short workout", "walk outside", "household chores", "speaking exercises")
DEFAULT_DIAL = 0.3
DEEP_WORK_TARGET_SLOTS = 12                    # the ideal day's deep work: 6 hours. A placeholder until the person sets one
IDEAL_BLOCK_SLOTS = 3                          # the longest comfortable screen run in the ideal day
WAKE_TOLERANCE = 3                            # slots the model's wake_slot may differ from the expected one
BUDGET_TOLERANCE = 2                           # slots either side of the deep-work budget that still count as matching


def slot_time(n: int) -> str:
    """Clock time at the start of slot n (slot 0 = 05:00; slots from 38 on are after midnight)."""
    m = (DAY_START_MIN + n * 30) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def to_slot(minutes_since_midnight_of_start_day: int) -> int:
    """The slot holding a moment, given minutes since 00:00 of the day the grid starts on (05:00 = slot 0)."""
    return (minutes_since_midnight_of_start_day - DAY_START_MIN) // 30


def slot_range_clock(first: int, last: int) -> str:
    return f"{slot_time(first)}-{slot_time(last + 1)}"


@dataclass(frozen=True)
class Fixed:
    """An event already on the calendar that the plan must not touch. End slot is exclusive."""
    title: str
    start: int
    end: int


def dial_numbers(real_deep_min: float, real_run_min: float, dial: float, target_slots: int = DEEP_WORK_TARGET_SLOTS) -> tuple[int, int]:
    """(deep-work budget in slots, typical block length in slots): what the person really does, pulled toward the ideal by the dial."""
    real_slots = real_deep_min / 30
    budget = round(real_slots + dial * (target_slots - real_slots))
    run_slots = max(real_run_min / 30, 1.0)
    block = round(run_slots + dial * (IDEAL_BLOCK_SLOTS - run_slots))
    return max(budget, 1), min(max(block, 1), IDEAL_BLOCK_SLOTS)


@dataclass
class PlanInputs:
    day: date
    fixed: list[Fixed] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)                 # id, text, due, slip_count, source
    preferences: list[tuple[str, str]] = field(default_factory=list)  # (section, text) from the persistent file
    latest_day: str = ""                                            # the newest finished day, practiced schedule as text
    recent_days: list[str] = field(default_factory=list)            # the days before it, one short block each
    patterns: str = ""
    durations: str = ""
    recent_work: list[str] = field(default_factory=list)            # what the devices show the person worked on, "label: minutes"
    last_plan_vs_final: str = ""                                    # only when the person changed yesterday's plan
    journal: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)           # `p:` notes since the last plan
    rules: list[str] = field(default_factory=list)                  # `r:` standing rules
    dial: float = DEFAULT_DIAL
    deep_work_target: int = DEEP_WORK_TARGET_SLOTS
    budget_slots: int = 0                                           # deep-work budget from dial_numbers; 0 = not set
    block_slots: int = 0
    last_night: str = ""                                            # last activity, bedtime and expected wake, as one text line
    expected_wake_slot: int | None = None                           # the slot of the expected wake-up; checked within WAKE_TOLERANCE
    feedback: str = ""                                              # re-plan only
    current_plan: list[dict] = field(default_factory=list)          # re-plan only
    replan_from: int | None = None                                  # re-plan only: the first slot to plan; slots before it are past


# --- the worked example (synthetic) ------------------------------------------------------------------------------------

EXAMPLE_FIXED = [Fixed("Dentist", 8, 10)]
EXAMPLE_BUDGET, EXAMPLE_BLOCK = 7, 2
EXAMPLE_OUTPUT = {'anchors': {'wake_slot': 4,
             'sleep_slot': 36,
             'meals': [{'name': 'Lunch', 'slot': 16, 'slots': 2, 'reason': 'default 13:00-14:00'},
                       {'name': 'Dinner', 'slot': 30, 'slots': 2, 'reason': 'default 20:00-21:00'}],
             'workout': {'slot': 4, 'slots': 1, 'reason': 'every morning right after waking'},
             'meditation': {'slot': 6, 'slots': 2, 'reason': "morning, before the first meal"}},
 'demand': [{'item': 'Renew passport form', 'slots': 2, 'why': 'due tomorrow, slipped 3 times', 'source': 'task 7'},
            {'item': 'Project coding', 'slots': 4, 'why': "deep goal work, the person's usual coding project", 'source': 'goal'},
            {'item': 'Study: system design', 'slots': 3, 'why': 'deep goal work', 'source': 'goal'},
            {'item': 'Shallow screen work: reading, applications, notes, planning',
             'slots': 6,
             'why': 'light work for the weak and risky hours',
             'source': 'goal'},
            {'item': 'Walk outside', 'slots': 1, 'why': 'outdoor block before meditation', 'source': 'break'},
            {'item': 'Household chores', 'slots': 1, 'why': 'off-screen after the passport form', 'source': 'break'},
            {'item': 'Sing songs', 'slots': 1, 'why': 'active break before the build session', 'source': 'break'},
            {'item': 'Short workout', 'slots': 1, 'why': 'off-screen after the build session', 'source': 'break'},
            {'item': 'Speaking exercises', 'slots': 1, 'why': 'vocal practice', 'source': 'break'},
            {'item': 'Walk outside (long)', 'slots': 2, 'why': 'evening walk before dinner', 'source': 'break'},
            {'item': 'Household chores: soak peanuts', 'slots': 1, 'why': 'nightly routine', 'source': 'break'}],
 'demand_vs_free': '23 slots of demand against 23 free: it fits, so nothing is cut',
 'windows': [{'slots': '10-15', 'clock': '10:00-13:00', 'rating': 'strong', 'evidence': '47% productive at 10:00 on 5 of 6 days'},
             {'slots': '18-23', 'clock': '14:00-17:00', 'rating': 'weak', 'evidence': 'adherence 19% on 14:00 starts, 13 blocks'},
             {'slots': '32-35', 'clock': '21:00-23:00', 'rating': 'risky', 'evidence': 'phone YouTube after dinner on 4 of 5 days'}],
 'placement_notes': 'Deep work (7 slots) in the strong window and one study stretch in the weak window after a break; only anchors, breaks and '
                    'shallow work in the risky evening.',
 'checklist': {'every_free_slot_filled': {'ok': True, 'fix': ''},
               'meditation_gap_respected': {'ok': True, 'fix': ''},
               'all_tasks_due_tomorrow_placed': {'ok': True, 'fix': ''},
               'deep_work_matches_dial': {'ok': True, 'fix': ''},
               'carryover_accounted_for': {'ok': True, 'fix': ''},
               'long_term_goal_served': {'ok': True, 'fix': ''},
               'no_screen_run_over_3_slots': {'ok': True, 'fix': ''},
               'modes_alternate_across_work_blocks': {'ok': True, 'fix': ''},
               'no_topic_repeated_in_same_half': {'ok': True, 'fix': ''},
               'all_work_blocks_at_least_2_slots': {'ok': True, 'fix': ''},
               'at_least_one_outdoor_block': {'ok': True, 'fix': ''}},
 'plan': [{'start_slot': 4, 'slots': 1, 'kind': 'anchor', 'title': 'Morning workout', 'mode': 'physical', 'why': 'daily workout, 20 minutes'},
          {'start_slot': 5,
           'slots': 1,
           'kind': 'break',
           'title': 'Walk outside',
           'mode': 'outdoor',
           'minutes': 20,
           'why': 'outdoor block before meditation'},
          {'start_slot': 6, 'slots': 2, 'kind': 'anchor', 'title': 'Meditation', 'mode': 'rest', 'why': 'daily one-hour meditation'},
          {'start_slot': 10,
           'slots': 2,
           'kind': 'work',
           'depth': 'shallow',
           'title': 'Renew passport form',
           'mode': 'build',
           'why': 'task due tomorrow, early because it slipped',
           'task_id': 7},
          {'start_slot': 12,
           'slots': 1,
           'kind': 'break',
           'title': 'Household chores',
           'mode': 'physical',
           'minutes': 15,
           'why': 'off-screen break after 2 screen slots'},
          {'start_slot': 13,
           'slots': 3,
           'kind': 'work',
           'depth': 'deep',
           'title': 'Project coding: API endpoints',
           'mode': 'build',
           'why': 'deep work in the strongest window'},
          {'start_slot': 16, 'slots': 2, 'kind': 'anchor', 'title': 'Lunch', 'mode': 'rest', 'why': 'default lunch time'},
          {'start_slot': 18, 'slots': 1, 'kind': 'break', 'title': 'Sing songs', 'mode': 'voice', 'minutes': 15, 'why': 'active break before study'},
          {'start_slot': 19,
           'slots': 3,
           'kind': 'work',
           'depth': 'deep',
           'title': 'Study: system design',
           'mode': 'study',
           'why': 'goal work in a weak window, after a break'},
          {'start_slot': 22, 'slots': 1, 'kind': 'break', 'title': 'Short workout', 'mode': 'physical', 'minutes': 10, 'why': 'off-screen break'},
          {'start_slot': 23,
           'slots': 1,
           'kind': 'work',
           'depth': 'deep',
           'title': 'Project coding: tests',
           'mode': 'build',
           'why': 'finish what the morning block started'},
          {'start_slot': 24, 'slots': 1, 'kind': 'break', 'title': 'Speaking exercises', 'mode': 'voice', 'minutes': 20, 'why': 'off-screen break'},
          {'start_slot': 25,
           'slots': 2,
           'kind': 'work',
           'depth': 'shallow',
           'title': 'Read the saved articles',
           'mode': 'study',
           'why': 'light study in a weak window'},
          {'start_slot': 27,
           'slots': 1,
           'kind': 'work',
           'depth': 'shallow',
           'title': 'Job applications',
           'mode': 'build',
           'why': 'short admin task, alternates with reading'},
          {'start_slot': 28,
           'slots': 2,
           'kind': 'break',
           'title': 'Walk outside',
           'mode': 'outdoor',
           'minutes': 30,
           'why': 'long break before dinner'},
          {'start_slot': 30, 'slots': 2, 'kind': 'anchor', 'title': 'Dinner', 'mode': 'rest', 'why': 'default dinner time'},
          {'start_slot': 32,
           'slots': 2,
           'kind': 'work',
           'depth': 'shallow',
           'title': "Review the day's notes",
           'mode': 'study',
           'why': 'light work in the risky evening'},
          {'start_slot': 34,
           'slots': 1,
           'kind': 'break',
           'title': 'Household chores: soak peanuts',
           'mode': 'physical',
           'minutes': 10,
           'why': 'evening routine, off-screen'},
          {'start_slot': 35,
           'slots': 1,
           'kind': 'work',
           'depth': 'shallow',
           'title': 'Plan tomorrow',
           'mode': 'build',
           'why': 'short wrap-up before sleep'}]}


SYSTEM = f"""You plan one full day for one person, as half-hour slots. The day runs from 05:00 to 05:00 and has {SLOTS} slots: slot 0 is 05:00, slot 4 is 07:00,
slot 10 is 10:00, slot 38 is 00:00 (midnight, the night after), slot 47 is 04:30. Slots from 38 on are after midnight but still belong to this day.
Everything you are given was measured by code, so never recompute or contradict it. You judge and write. You never ask questions: you always
produce a plan. You always answer in slot numbers, and where a clock time is asked for it must match the slots exactly.

How to work, in this exact order (your JSON fields follow the same order, facts before the plan):
1. "anchors": wake_slot and sleep_slot (wake_slot: when the person is expected to wake, given under LAST NIGHT; sleep_slot: the usual bedtime from the
   sleep pattern, 48 if it is not before the day ends), then meals, workout and meditation, each with slots and a one-line reason. Defaults: lunch
   13:00-14:00, dinner 20:00-21:00; use another time only if history or preferences support it. Workout and meditation are mandatory, at their usual
   time. Meditation is on an empty stomach: either in the morning before the first meal, or in the evening before dinner and at least
   {MEDITATION_GAP_SLOTS} slots (4 hours) after lunch ended. Dinner may move later to make room for it, but must start by slot {LATEST_DINNER_SLOT} (21:30).
2. "demand": a list of everything that will fill the free slots (from wake_slot up to sleep_slot, minus FIXED slots, minus the anchors): tasks (due
   soonest and most slipped first), deep goal work, shallow screen work, and active breaks. List every break as its own item (e.g. "Sing songs", 1 slot), never one lump of "breaks". Their "slots" must add up to exactly that many free slots. Deep work totals the DEEP WORK BUDGET given below (2 slots either way is fine).
   Use the REAL DURATIONS and the person's real run lengths for sizes. "demand_vs_free" says whether it fits and what is cut.
3. "windows": rate stretches of the day strong, weak or risky from the patterns and recent days. Each has "slots" ("first-last"), "clock" (the same
   stretch in clock time, e.g. slots 10-15 are "10:00-13:00": slot 0 is 05:00, slot n starts at 05:00 + n x 30 minutes, and the end is the start of slot last+1) and evidence with
   counts ("4 of 5 days").
4. "placement_notes": every deep block lies entirely inside a window you rated strong or weak, never in a risky or unrated stretch (only anchors, breaks
   and shallow work go there). Tasks that slipped often go early.
5. "checklist": for each line say ok true or false, and if false what fix you made in the plan. Be honest: the code checks the plan too.
6. "plan": the final blocks.

Rules for the plan:
- Cover every FREE slot from wake_slot up to sleep_slot with exactly one block. Never use a slot marked FIXED, and never overlap blocks.
- Keep the person busy: no planned leisure, no "free time", no optional blocks. Every block goes on a calendar, so each has a short "why".
- kind is "anchor" (meal, workout, meditation), "work", or "break". mode is one of: {", ".join(MODES)}. A "work" block also has "depth": "deep"
  (hard thinking or building that moves the goal) or "shallow" (admin, reading, notes, applications, planning).
- Screen work (modes study and build) never runs more than {MAX_SCREEN_RUN} slots in a row; then put an off-screen block. Meals, meditation and workout count as
  off-screen blocks, so use them before adding breaks.
- Keep screen-work blocks grouped and long. Each work block must be at least 2 slots unless it is the last block before an anchor (meal, sleep) that
  forces a shorter gap. Do not scatter 1-slot screen blocks across the schedule.
- Do not put the same screen mode (study or build) in two consecutive work blocks. One study block, then a break or anchor, then a build block — or
  vice versa. A break or anchor between two same-mode blocks is not enough: the modes must actually alternate across work blocks.
- Each topic or project appears in at most one work block per half of the day (morning = wake to lunch, afternoon/evening = lunch to sleep). Do not
  split "Study: VLLM" into two separate blocks or repeat "Build: personal-agent" twice in the evening. If you need more time on the same topic,
  make that block longer, up to 3 slots.
- Breaks are active, never leisure. Pick only from this menu: {", ".join(BREAK_MENU)}. "Rest" is not a break. A break takes one slot (at most two,
  for a long walk or chore) and has "minutes" (5 to 30, 5 only for a tiny routine such as soaking peanuts) for the activity itself; the rest of its slot is transition. Never stretch a break to fill
  time: if slots are left over, give them to work blocks, still keeping screen runs to 3 slots with a break or anchor between. Its title names a menu item. A short meditation or short workout break does not replace the mandatory ones.
- The whole 24 hours counts: outdoor items may go at any hour. Include at least one outdoor block.
- Name work after what the person really works on: use WHAT THE DEVICES SHOW THE PERSON WORKED ON and the goals. Do not invent topics, projects or courses.
  A title is a plain action, like "Study: VLLM architecture" or "Build: personal-agent". Never use app names, chat titles or labels like "ChatGPT:" or "VS Code:" in a title.
- Put a task on a block by copying its number into "task_id" (tasks are listed as [task N]). One block per task. Leave "task_id" out otherwise.
- Instructions marked INSTRUCTIONS FOR THIS PLAN must be followed. STANDING RULES always apply.
- Serve a long-term goal from the PREFERENCES AND GOALS section in the plan at least once.
- If a CURRENT PLAN and FEEDBACK are given, first write "feedback_constraints" (the feedback as short constraints), then keep every block the feedback does not touch exactly as it is.
- Do not write anything outside the JSON object.

The code rejects the plan if any of these is true, so check each one before answering:
- a FREE slot from wake_slot up to sleep_slot has no block, a block uses a FIXED slot, or two blocks overlap;
- start_slot or slots is not a whole number, or slots is below 1;
- a break is longer than 2 slots, its title does not name a menu item, or its minutes are outside 5-30;
- the same screen mode (study or build) is in two touching blocks, or screen work runs more than {MAX_SCREEN_RUN} slots in a row;
- meditation is not in the morning before the first meal or in the evening before dinner (4 hours after lunch), or dinner starts after slot {LATEST_DINNER_SLOT};
- there is no workout, no meditation or no outdoor block;
- deep work is outside the DEEP WORK BUDGET range, or sits outside a window you rated strong or weak;
- a task that is due has no block, or a task is on two blocks.

Answer with one JSON object with exactly these keys in this order: "anchors", "demand", "demand_vs_free", "windows", "placement_notes", "checklist", "plan"
(and "feedback_constraints" first when re-planning).

The checklist must include these keys (in this order): every_free_slot_filled, meditation_gap_respected, all_tasks_due_tomorrow_placed,
deep_work_matches_dial, carryover_accounted_for, long_term_goal_served, no_screen_run_over_3_slots, modes_alternate_across_work_blocks,
no_topic_repeated_in_same_half, all_work_blocks_at_least_2_slots, at_least_one_outdoor_block.

EXAMPLE
Day with a fixed event "Dentist" in slots 8-9 (07:00 is slot 4), one open task [task 7] "Renew passport form" (due tomorrow, slipped 3 times), IDEAL_WEIGHT 0.3,
DEEP WORK BUDGET {EXAMPLE_BUDGET} slots, typical block {EXAMPLE_BLOCK} slots.
Answer:
{json.dumps(EXAMPLE_OUTPUT, ensure_ascii=False)}"""


def system_message() -> str:
    return SYSTEM


# --- the data the model reads -------------------------------------------------------------------------------------------

def grid_lines(day: date, fixed: list[Fixed]) -> list[str]:
    taken = {s: f.title for f in fixed for s in range(f.start, f.end)}
    return [f"{n:2d} {slot_time(n)}{' (after midnight)' if n >= 38 else ''} {'FIXED: ' + taken[n] if n in taken else 'FREE'}" for n in range(SLOTS)]


def render_user(i: PlanInputs) -> str:
    out = [f"TARGET DAY: {i.day:%A %Y-%m-%d} (IST)",
           f"IDEAL_WEIGHT: {i.dial:g}  (0 = plan only from what the person actually did, 1 = plan the ideal day from the preferences)"]
    if i.budget_slots:
        out.append(f"DEEP WORK BUDGET (computed by code from what the person really does and the ideal weight): {i.budget_slots} slots of deep work in total "
                   f"(study and build blocks marked deep); {max(i.budget_slots - 2, 0)} to {i.budget_slots + 2} is fine.")
    if i.block_slots:
        out.append(f"TYPICAL BLOCK LENGTH (computed by code): {i.block_slots} slots; use 1 to {IDEAL_BLOCK_SLOTS}. Longer than that rarely holds.")
    if i.last_night:
        out += ["", "LAST NIGHT (computed by code from both devices)", i.last_night]
    out += ["", f"SLOT GRID for the day {i.day:%A %Y-%m-%d}, 05:00 to 05:00 (slot number, start time, state)"] + grid_lines(i.day, i.fixed)
    fixed_slots = len({s for f in i.fixed for s in range(f.start, f.end)})
    out += ["", f"{SLOTS - fixed_slots} slots are not FIXED (before wake and sleep are taken out)."]

    out += ["", "OPEN TASKS (due soonest first)"]
    out += [f"[task {t['id']}] {t['text']} | due {t.get('due') or 'no date'} | slipped {t.get('slip_count', 0)} times | from {t.get('source', 'tasks')}"
            for t in i.tasks] or ["(none)"]

    out += ["", "PREFERENCES AND GOALS (the person's own file)"]
    out += [f"[{sec}] {text}" for sec, text in i.preferences] or ["(empty)"]
    if i.rules:
        out += ["", "STANDING RULES"] + [f"- {r}" for r in i.rules]
    if i.instructions:
        out += ["", "INSTRUCTIONS FOR THIS PLAN"] + [f"- {x}" for x in i.instructions]
    out += ["", "WHAT THE DEVICES SHOW THE PERSON WORKED ON (last 14 days, minutes)"] + ([f"- {w}" for w in i.recent_work] or ["(nothing measured)"])

    out += ["", "LATEST FINISHED DAY, IN FULL (what was planned against what the devices show)", i.latest_day or "(no data)"]
    if i.last_plan_vs_final:
        out += ["", "WHAT THE PERSON CHANGED IN THE LAST GENERATED PLAN", i.last_plan_vs_final]
    out += ["", "THE DAYS BEFORE THAT (short)"] + (i.recent_days or ["(none)"])
    out += ["", "PATTERNS (each with how many days or blocks it rests on)", i.patterns or "(not enough history)"]
    if i.durations:
        out += ["", "REAL DURATIONS", i.durations]
    out += ["", "JOURNAL, LAST 2 DAYS"] + ([f"- {j}" for j in i.journal] or ["(none)"])

    if i.replan_from is not None:
        out += ["", f"RE-PLAN FROM SLOT {i.replan_from} ({slot_time(i.replan_from)}). The day is under way: slots before {i.replan_from} are past and marked FIXED. "
                f"Set wake_slot to {i.replan_from} and plan only from there to sleep_slot. Workout, meditation and meals that already happened are in "
                "the past, so add them only if their usual time is still ahead. Due tasks should still get a block if there is room."]
    if i.current_plan or i.replan_from is not None:
        out += ["", "CURRENT PLAN FOR THE REST OF THE DAY (keep what the feedback does not touch)",
                json.dumps(i.current_plan, ensure_ascii=False) if i.current_plan else "(nothing planned)", "", f"FEEDBACK: {i.feedback}"]
    out += ["", f"Re-plan the rest of {i.day:%A %Y-%m-%d}." if i.replan_from is not None else f"Plan {i.day:%A %Y-%m-%d}."]
    return "\n".join(out)


def build_messages(i: PlanInputs) -> list[Message]:
    return [Message("system", system_message()), Message("user", render_user(i))]
