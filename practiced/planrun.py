"""The nightly plan: gather everything from the database, ask the model, check the answer, save it as the day's plan.

The plan is for the logical day that starts at 05:00 on the run's calendar date. The job runs while the person sleeps (02:00 and
03:00 IST), so the newest day it knows is still open or has just closed; it is built live and handed to the model in full.
A bad answer is re-asked up to twice with the violations listed; after that the call raises and the night job reports a failure
(no template fallback). Saved as an `llm` draft (inputs.planner = true) and accepted at once, like the earlier unattended generator.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

import storage
from config import load_config
from digital.builder import day_window
from llm.client import Message
from practiced import store
from practiced.digest import render as render_day
from practiced.durations import real_durations, render_durations
from practiced.llmcall import BadReply, call_json
from practiced.patterns import compute_patterns, recent_work, render_patterns
from practiced.plancheck import check, repair, to_blocks
from practiced.planning import DEFAULT_DIAL, PROMPT_VERSION, SLOTS, Fixed, PlanInputs, build_messages, dial_numbers, to_slot
from schedule_gen.model import PENDING, DraftEntry

IST = ZoneInfo("Asia/Kolkata")
KIND = "plan"
MAX_TOKENS = 9000                  # hidden reasoning tokens count inside this
TEMPERATURE = 0.3
RETRIES = 2
HISTORY_DAYS = 90
RECENT_SHOWN = 4                   # short lines for the days before the latest one
BED_AFTER_LAST_ACTIVITY = timedelta(minutes=30)
DEFAULT_SLEEP_MIN = 420


class PlanError(RuntimeError):
    """The model could not produce a valid plan after the retries."""


def plan_day(now: datetime) -> date:
    """The logical day (05:00 to 05:00) being planned: the run's calendar date. A run at 02:00 or 03:00 plans the day that starts at 05:00."""
    return now.astimezone(IST).date()


def recent_day_line(p: dict) -> str:
    """One short line of a finished day: verdicts, biggest sinks, best stretch, productive and phone minutes."""
    v = "; ".join(f"{b['title']} {b['verdict']}" + (f" {b['on_plan_pct']}%" if b["verdict"] in ("DONE", "PARTIAL", "OFF-PLAN") else "") for b in p["plan"])
    sinks = ", ".join(f"{s['what']} {s['minutes']}m" for s in p.get("sinks", []))
    bs = p.get("best_stretch")
    phone = p["totals"]["phone"]
    return (f"{p['day']} ({date.fromisoformat(p['day']):%a}): {v or 'no calendar blocks'} | biggest time sinks: {sinks or 'none'} | "
            f"best stretch: {(bs['start'] + '-' + bs['end'] + ' ' + bs['category'] + ' ' + str(bs['minutes']) + 'm') if bs else 'none'} | "
            f"productive laptop {p['stats']['productive_laptop_min']}m, phone social/media {phone.get('social', 0) + phone.get('media', 0):.0f}m")


# --- gathering ------------------------------------------------------------------------------------------------------------

async def _existing(pool, day: date) -> list[dict]:
    lo, hi = day_window(day)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id, title, start_at, end_at, origin FROM schedule WHERE deleted_at IS NULL AND start_at < %s AND end_at > %s ORDER BY start_at", (hi, lo))
            return await cur.fetchall()


async def already_planned(pool, day: date) -> str | None:
    if await storage.get_open_draft(pool, day):
        return "draft_open"
    if any(r["origin"] == "generated" for r in await _existing(pool, day)):
        return "day_already_planned"
    return None


def _fixed(rows: list[dict], day: date) -> list[Fixed]:
    lo, _ = day_window(day)
    out = []
    for r in rows:
        first = max(int((r["start_at"] - lo).total_seconds() // 1800), 0)
        last = min(-(-int((r["end_at"] - lo).total_seconds()) // 1800), SLOTS)
        if last > first:
            out.append(Fixed(r["title"], first, last))
    return out


async def _notes(pool, tag: str, since: datetime) -> list[str]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT text FROM notes WHERE tags @> %s::jsonb AND created_at >= %s ORDER BY created_at", (json.dumps([tag]), since))
            return [r[0] for r in await cur.fetchall()]


async def _last_activity(pool, now: datetime) -> datetime | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT max(coalesce(ts_end, ts_start)) FROM events WHERE kind <> 'idle' AND ts_start <= %s AND ts_start >= %s",
                              (now, now - timedelta(hours=30)))
            return (await cur.fetchone())[0]


async def _intended_sleep(pool, day: date) -> datetime | None:
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT payload->>'intended_sleep' FROM phone_answer WHERE kind = 'evening' AND day = %s AND payload ? 'intended_sleep' "
                                  "ORDER BY answered_at DESC LIMIT 1", (day,))
                row = await cur.fetchone()
        return datetime.fromisoformat(row[0]).astimezone(IST) if row and row[0] else None
    except Exception:
        return None


async def last_night(pool, now: datetime, day: date, patterns: dict, latest: date) -> tuple[str, int | None]:
    """The line about last night and the slot the person is expected to wake in. Bedtime is 30 minutes after the last activity on
    either device (never earlier than now if they are still up); the wake is bedtime plus the usual sleep."""
    last = await _last_activity(pool, now)
    if last is None:
        return "", None
    last = last.astimezone(IST)
    bed = max(last + BED_AFTER_LAST_ACTIVITY, now) if last > now - BED_AFTER_LAST_ACTIVITY else last + BED_AFTER_LAST_ACTIVITY
    sleep = patterns["sleep"]
    minutes = sleep["median_minutes"] if sleep["n"] >= 3 else DEFAULT_SLEEP_MIN
    wake = bed + timedelta(minutes=minutes)
    slot = max(0, min(to_slot(int((wake - datetime.combine(day, time(0, 0), IST)).total_seconds() // 60)), SLOTS - 1))
    text = (f"Last activity on either device was at {last:%H:%M}. Bedtime is taken as 30 minutes later, {bed:%H:%M}"
            f"{' (the person is still up, so it is not before now)' if bed == now else ''}. At the usual {minutes // 60}h{minutes % 60:02d} of sleep"
            f"{' (median of the last ' + str(sleep['n']) + ' nights)' if sleep['n'] >= 3 else ''} the person is expected to wake about {wake:%H:%M}, "
            f"which is slot {slot}. wake_slot must be within 3 slots of {slot}.")
    intended = await _intended_sleep(pool, latest)
    if intended:
        text += f" On the evening sheet the person said they planned to sleep at {intended:%H:%M}."
    return text, slot


def _latest_in_progress(now: datetime, day: date) -> str | None:
    """When the run is before 05:00 IST (yesterday's logical day has not fully closed), return a note for the prompt."""
    day_start = datetime.combine(day, time(5, 0), IST)
    if now < day_start:
        mins_left = int((day_start - now).total_seconds() / 60)
        return f"(in progress: device data captured up to {now:%H:%M} IST; the last ~{mins_left} minutes of this day are not yet recorded)"
    return None


async def gather(pool, now: datetime, client=None, *, day: date | None = None) -> tuple[PlanInputs, dict]:
    """Everything the planning call reads. `day` defaults to the day the night run plans; a re-plan passes the day under way."""
    now = now.astimezone(IST)
    day = day or plan_day(now)
    latest = day - timedelta(days=1)
    cfg = load_config().planner
    dial, target_slots = cfg.dial, cfg.deep_work_target_slots
    stored = {d["day"]: d for d in await store.load_history(pool, day, HISTORY_DAYS)}
    live = await store.build_day(pool, latest, client=client)
    in_progress_note = _latest_in_progress(now, day)
    stored[live["day"]] = live
    days = [stored[k] for k in sorted(stored)]
    patterns = compute_patterns(days, day)
    budget, block = dial_numbers(patterns["daily_productive"]["median_min"], patterns["run_lengths"].get("p75", 30), dial,
                                 target_slots=target_slots)

    prefs = [(e["section"], e["text"]) for e in await storage.list_persistent_entries(pool, include_retired=False)]
    tasks = []
    someday = await storage.someday_clause(pool)          # tasks parked as "someday" are not planned
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id, text, due_at, slip_count FROM tasks WHERE completed_at IS NULL AND dropped_at IS NULL" + someday +
                              " ORDER BY due_at NULLS LAST, slip_count DESC, id LIMIT 12")
            tasks = [{"id": r["id"], "text": r["text"], "due": r["due_at"].astimezone(IST).date().isoformat() if r["due_at"] else None,
                      "slip_count": r["slip_count"], "source": "tasks"} for r in await cur.fetchall()]
    text_night, wake_slot = await last_night(pool, now, day, patterns, latest)
    ch = live.get("plan_changes")
    changed = json.dumps({k: v for k, v in ch.items() if v}) if ch and (ch["edited_after_accepting"] or ch["discarded"] or ch["added_by_user"] or ch["retimed_in_draft"]) else ""
    older = [d for d in days if d["day"] < live["day"]][-RECENT_SHOWN:]
    latest_text = (in_progress_note + "\n" if in_progress_note else "") + render_day(live)

    inp = PlanInputs(
        day=day, fixed=_fixed(await _existing(pool, day), day), tasks=tasks, preferences=prefs, latest_day=latest_text,
        recent_days=[recent_day_line(p) for p in older], patterns=render_patterns(patterns),
        durations=render_durations(real_durations(days)), recent_work=recent_work(days, day), last_plan_vs_final=changed,
        journal=await _notes(pool, "journal", now - timedelta(days=2)), instructions=await _notes(pool, "plan", now - timedelta(days=1)),
        rules=await _notes(pool, "rule", now - timedelta(days=3650)), dial=dial, budget_slots=budget, block_slots=block,
        last_night=text_night, expected_wake_slot=wake_slot,
    )
    return inp, {"history_days": len(days), "budget_slots": budget, "latest_day": live["day"], "prompt_version": PROMPT_VERSION,
                 "dial": dial, "in_progress": bool(in_progress_note)}


# --- asking ---------------------------------------------------------------------------------------------------------------

async def ask(client, inp: PlanInputs, *, retries: int = RETRIES) -> tuple[dict, Any, int]:
    """(the checked answer, the last completion, how many re-asks it took). Raises PlanError when it never passes."""
    messages = build_messages(inp)
    problems: list[str] = []
    for attempt in range(retries + 1):
        try:
            out, done = await call_json(client, messages, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
            reply = done.text
        except BadReply as e:
            out, done, reply, problems = None, None, e.text, [str(e)]
        else:
            fixed = repair(out, inp)          # trivial mistakes are fixed by code, not re-asked
            problems = check(out, inp)
            if not problems:
                if fixed:
                    out["code_repairs"] = fixed
                return out, done, attempt
        if attempt == retries:
            break
        messages += [Message("assistant", reply), Message("user", "That plan cannot be used:\n- " + "\n- ".join(problems) +
                                                           "\nAnswer again with the one JSON object, fixed. Keep everything that was not wrong.")]
    raise PlanError("plan rejected after retries: " + "; ".join(problems[:6]))


async def run_plan_once(pool, client, now: datetime) -> dict[str, Any]:
    """Plan the day if it has no plan yet. Raises on a model failure so the nightly step is recorded as failed."""
    from schedule_gen import service

    now = now.astimezone(IST)
    day = plan_day(now)
    skip = await already_planned(pool, day)
    if skip:
        return {"skipped": skip, "day": day.isoformat()}
    inp, summary = await gather(pool, now, client)
    out, done, reasked = await ask(client, inp)
    entries = [DraftEntry(title=b["title"], start_at=b["start"], end_at=b["end"], reason=b["why"], state=PENDING, task_id=b["task_id"]).to_json()
               for b in to_blocks(out, inp)]
    reasoning = {k: out[k] for k in ("anchors", "demand", "demand_vs_free", "windows", "placement_notes", "checklist", "code_repairs") if k in out}
    draft = await storage.insert_draft(
        pool, target_day=day, source="llm", model=done.model, instruction="", entries=entries, rejected=[],
        inputs={**summary, "planner": True, "reasked": reasked, "reasoning": reasoning},
    )
    accepted = await service.accept(pool, draft["id"], None)
    return {"day": day.isoformat(), "draft_id": draft["id"], "model": done.model, "blocks": len(entries), "reasked": reasked,
            "accepted": len(accepted["accepted"]), "conflicts": len(accepted["conflicts"])}
