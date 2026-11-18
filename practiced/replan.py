"""Re-plan the rest of the day when the person asks from the phone, and undo a re-plan.

The phone stores a request (replan_request, with an optional note such as "feeling sick, drop the gym") and starts the GitHub
job `replan`, which runs calendar_pull -> replan -> calendar_push. Only the newest pending request is handled; older pending
ones are superseded, and one older than STALE has expired (the person has moved on).

A re-plan starts at the next half-hour slot. It never touches the past, the person's own events, or a block they edited:
  - generated blocks starting at or after that slot are removed (soft-deleted, so the calendar push removes the events);
  - a generated block running across it is cut short there;
  - the model plans the free slots from there to bedtime, with the old blocks and the note as CURRENT PLAN and FEEDBACK,
    checked like the night plan (minus the whole-day rules: mandatory anchors, the deep-work budget, due tasks).
What was removed and cut is kept on the request, so Undo can put it back: it removes the re-plan's blocks (unless the person
has edited them since), restores the cut ends, and re-creates the removed blocks as new rows (new rows, because the old
ones' calendar events are gone). Only the latest re-plan can be undone. Nothing changes if the model never gives a valid plan.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import storage
from digital.builder import day_window
from llm.approved import ApprovalRequired, QuotaExceeded
from practiced.planning import SLOTS, Fixed
from practiced.planrun import PlanError, _fixed, ask, gather
from practiced.plancheck import to_blocks
from schedule_gen.model import PENDING, DraftEntry

IST = ZoneInfo("Asia/Kolkata")
STALE = timedelta(minutes=30)
NO_NOTE = ("No note. The person asked to re-plan the rest of the day, most likely because they fell behind: fit what matters most "
           "into the time that is left.")
NOT_RETRIED = (PlanError, QuotaExceeded, ApprovalRequired)


class ReplanError(RuntimeError):
    """A request that cannot be carried out; the message is shown on the phone."""


def logical_day(now: datetime) -> date:
    """The 05:00-to-05:00 day under way at `now` (01:00 still belongs to yesterday)."""
    return (now.astimezone(IST) - timedelta(hours=5)).date()


def first_slot(now: datetime, day: date) -> int:
    """The first whole half-hour slot that has not started yet."""
    minutes = (now - day_window(day)[0]).total_seconds() / 60
    return int(-(-minutes // 30))


# --- requests -------------------------------------------------------------------------------------------------------------

async def take_next(pool) -> dict | None:
    """The newest pending request, marking older pending ones superseded and stale ones expired."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("UPDATE replan_request SET status = 'expired', finished_at = now() "
                              "WHERE status = 'pending' AND requested_at < now() - %s", (STALE,))
            await cur.execute("SELECT id, kind, note, target FROM replan_request WHERE status = 'pending' ORDER BY id DESC LIMIT 1 FOR UPDATE")
            req = await cur.fetchone()
            if req:
                await cur.execute("UPDATE replan_request SET status = 'superseded', finished_at = now() WHERE status = 'pending' AND id < %s",
                                  (req["id"],))
    return req


async def _finish(pool, req_id: int, status: str, *, error: str | None = None, result: dict | None = None) -> None:
    async with pool.connection() as conn:
        await conn.execute("UPDATE replan_request SET status = %s, error = %s, result = %s, finished_at = now() WHERE id = %s",
                           (status, error, Jsonb(result or {}), req_id))


async def process_pending(pool, client, now: datetime, *, attempts: int = 1) -> dict[str, Any]:
    """Handle the newest pending request. A failure is written on the request (the phone shows it) and raised (the job logs it)."""
    req = await take_next(pool)
    if req is None:
        return {"skipped": "no pending request"}
    for attempt in range(1, attempts + 1):
        try:
            result = await (replan(pool, client, now, req["note"]) if req["kind"] == "replan" else undo(pool, req["target"]))
            break
        except Exception as exc:
            if attempt < attempts and not isinstance(exc, (*NOT_RETRIED, ReplanError)):
                continue
            await _finish(pool, req["id"], "failed", error=(str(exc) if isinstance(exc, ReplanError) else f"{type(exc).__name__}: {exc}")[:500])
            raise
    await _finish(pool, req["id"], "done", result=result)
    return {"request": req["id"], "kind": req["kind"], **{k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}}


# --- re-plan --------------------------------------------------------------------------------------------------------------

async def _rows(pool, day: date) -> list[dict]:
    lo, hi = day_window(day)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id, title, start_at, end_at, origin, edited_by_user, task_id, why, "
                              "to_jsonb(schedule)->>'plan_kind' AS plan_kind, to_jsonb(schedule)->>'plan_mode' AS plan_mode FROM schedule "
                              "WHERE deleted_at IS NULL AND start_at < %s AND end_at > %s ORDER BY start_at", (hi, lo))
            return await cur.fetchall()


def _slot_of(t: datetime, day: date) -> int:
    return int((t - day_window(day)[0]).total_seconds() // 1800)


def _end_slot(t: datetime, day: date) -> int:
    return -(-int((t - day_window(day)[0]).total_seconds()) // 1800)


def _saved(r: dict) -> dict:
    return {"title": r["title"], "start_at": r["start_at"].isoformat(), "end_at": r["end_at"].isoformat(), "task_id": r["task_id"], "why": r["why"],
            "kind": r["plan_kind"], "mode": r["plan_mode"]}


async def replan(pool, client, now: datetime, note: str) -> dict[str, Any]:
    from schedule_gen import service

    now = now.astimezone(IST)
    day = logical_day(now)
    n = first_slot(now, day)
    if n >= SLOTS:
        raise ReplanError("The day is over: there is nothing left to re-plan.")
    cut = day_window(day)[0] + timedelta(minutes=30 * n)
    rows = await _rows(pool, day)
    movable = [r for r in rows if r["origin"] == "generated" and not r["edited_by_user"] and r["end_at"] > cut]
    remove = [r for r in movable if r["start_at"] >= cut]
    trim = [r for r in movable if r["start_at"] < cut]
    kept = [r for r in rows if r not in movable and r["end_at"] > cut]

    inp, summary = await gather(pool, now, client, day=day)
    inp.fixed = [Fixed("already past", 0, n)] + [f for f in _fixed(kept, day) if f.end > n]
    inp.replan_from, inp.expected_wake_slot, inp.last_night, inp.budget_slots = n, None, "", 0
    inp.current_plan = [{"start_slot": max(_slot_of(r["start_at"], day), n), "slots": max(1, _end_slot(r["end_at"], day) - max(_slot_of(r["start_at"], day), n)),
                         "title": r["title"]} for r in sorted(trim + remove, key=lambda r: r["start_at"])]
    inp.feedback = note.strip() or NO_NOTE
    out, done, reasked = await ask(client, inp)
    entries = [DraftEntry(title=b["title"], start_at=b["start"], end_at=b["end"], reason=b["why"], state=PENDING, task_id=b["task_id"],
                        kind=b["kind"], mode=b["mode"]).to_json()
               for b in to_blocks(out, inp) if b["start"] >= cut]

    # The model's plan passed the checks: only now is anything changed.
    for r in remove:
        await storage.delete_schedule_entry(pool, r["id"])
    for r in trim:
        await storage.update_schedule_entry(pool, r["id"], title=r["title"], start_at=r["start_at"], end_at=cut)
    reasoning = {k: out[k] for k in ("feedback_constraints", "anchors", "demand", "windows", "placement_notes", "code_repairs") if k in out}
    draft = await storage.insert_draft(
        pool, target_day=day, source="llm", model=done.model, instruction=note.strip(), entries=entries, rejected=[],
        inputs={**summary, "planner": True, "replan": True, "from_slot": n, "reasked": reasked, "reasoning": reasoning},
    )
    accepted = await service.accept(pool, draft["id"], None)
    return {"day": day.isoformat(), "from": cut.isoformat(), "removed": [_saved(r) for r in remove],
            "trimmed": [{"id": r["id"], "end_at": r["end_at"].isoformat()} for r in trim], "added": accepted["accepted"],
            "conflicts": len(accepted["conflicts"]), "draft_id": draft["id"], "model": done.model, "reasked": reasked}


# --- undo -----------------------------------------------------------------------------------------------------------------

async def undo(pool, target: int | None) -> dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id, kind, status, result FROM replan_request WHERE id = %s", (target,))
            req = await cur.fetchone()
            if req is None or req["kind"] != "replan" or req["status"] != "done":
                raise ReplanError(f"That re-plan cannot be undone ({req['status'] if req else 'not found'}).")
            await cur.execute("SELECT 1 FROM replan_request WHERE kind = 'replan' AND status = 'done' AND id > %s", (target,))
            if await cur.fetchone():
                raise ReplanError("Only the latest re-plan can be undone.")
    res = req["result"]

    removed = kept_edited = 0
    for sid in res.get("added", []):
        row = await storage.get_entry(pool, sid)
        if row is None or row["deleted_at"] is not None:
            continue
        if row.get("edited_by_user"):
            kept_edited += 1                         # the person changed it after the re-plan: theirs now
            continue
        await storage.delete_schedule_entry(pool, sid)
        removed += 1

    for t in res.get("trimmed", []):
        row = await storage.get_entry(pool, t["id"])
        if row is not None and row["deleted_at"] is None and row["end_at"] == datetime.fromisoformat(res["from"]):
            await storage.update_schedule_entry(pool, t["id"], title=row["title"], start_at=row["start_at"], end_at=datetime.fromisoformat(t["end_at"]))

    restored = clashed = 0
    for r in res.get("removed", []):
        start, end = datetime.fromisoformat(r["start_at"]), datetime.fromisoformat(r["end_at"])
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 FROM schedule WHERE deleted_at IS NULL AND start_at < %s AND end_at > %s LIMIT 1", (end, start))
                if await cur.fetchone():
                    clashed += 1
                    continue
        entry = await storage.create_schedule_entry(pool, title=r["title"], start_at=start, end_at=end, origin="generated", task_id=r.get("task_id"))
        if r.get("why"):
            async with pool.connection() as conn:
                await conn.execute("UPDATE schedule SET why = %s WHERE id = %s", (r["why"], entry["id"]))
        if r.get("kind"):
            try:
                async with pool.connection() as conn:
                    await conn.execute("UPDATE schedule SET plan_kind = %s, plan_mode = %s WHERE id = %s", (r["kind"], r.get("mode"), entry["id"]))
            except psycopg.errors.UndefinedColumn:
                pass
        restored += 1

    async with pool.connection() as conn:
        await conn.execute("UPDATE replan_request SET status = 'undone' WHERE id = %s", (target,))
    return {"undid": target, "removed": removed, "kept_edited": kept_edited, "restored": restored, "clashed": clashed}
