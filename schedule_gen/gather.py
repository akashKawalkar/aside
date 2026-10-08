# schedule_gen/gather.py — step 1 of plan 3.6: code collects everything a draft may depend on, once, as plain data.
# The context sources (context/sources/schedule_gen.py) and the placeholder generator both read this same object, so the
# LLM path and the baseline see identical facts.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import storage
from sessions.task_lifecycle import stale_reason
from schedule_gen.model import IST, PENDING, Block, DraftEntry, Rules, free_slots

TEMPLATE_LOOKBACK_WEEKS = 4


@dataclass
class GenInputs:
    rules: Rules
    existing: list[dict[str, Any]] = field(default_factory=list)       # schedule rows already on the day (any origin)
    template_day: date | None = None                                    # the last same weekday that had something worth copying
    template: list[dict[str, Any]] = field(default_factory=list)       # its entries: {title, start_at, end_at}
    tasks: list[dict[str, Any]] = field(default_factory=list)          # pending, due on the day or overdue
    instructions: list[str] = field(default_factory=list)              # instruction texts covering the day
    candidates: list[dict[str, Any]] = field(default_factory=list)     # candidate items valid for the day
    locked: list[DraftEntry] = field(default_factory=list)             # pending entries the user edited: never regenerated
    current: list[DraftEntry] = field(default_factory=list)            # the rest of the open draft: kept unless the instruction needs a change
    fixed: list[Block] = field(default_factory=list)                    # existing rows + locked entries: nothing may overlap these
    free: list[tuple[datetime, datetime]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """What was offered, for the draft log: ids and counts, never the text itself."""
        return {
            "day": self.rules.day.isoformat(),
            "existing_ids": [r["id"] for r in self.existing],
            "template_day": self.template_day.isoformat() if self.template_day else None,
            "template_count": len(self.template),
            "task_ids": [t["id"] for t in self.tasks],
            "instruction_count": len(self.instructions),
            "candidate_ids": [c["id"] for c in self.candidates],
            "locked_count": len(self.locked),
            "current_count": len(self.current),
            "free_slots": len(self.free),
        }


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, IST)
    return start, start + timedelta(days=1)


def _in_range(row: dict[str, Any], day: date) -> bool:
    return (row.get("valid_from") is None or row["valid_from"] <= day) and (row.get("valid_until") is None or row["valid_until"] >= day)


def _as_dt(value: Any) -> datetime:
    return (datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone(IST)


async def find_template(pool, day: date) -> tuple[date | None, list[dict[str, Any]]]:
    """The most recent of the last few same weekdays that has entries the user stood behind (made or edited by them).
    Prefers that day's end-of-day snapshot; falls back to the schedule as it stands."""
    for weeks in range(1, TEMPLATE_LOOKBACK_WEEKS + 1):
        past = day - timedelta(weeks=weeks)
        rows = await storage.get_schedule_snapshot(pool, day=past)
        if rows is None:
            start, end = _day_bounds(past)
            rows = await storage.list_schedule_range(pool, start=start, end=end)
        mine = [r for r in rows if r.get("origin", "user") == "user" or r.get("edited_by_user")]
        if mine:
            return past, [{"title": r["title"], "start_at": _as_dt(r["start_at"]), "end_at": _as_dt(r["end_at"])} for r in mine]
    return None, []


async def gather(pool, day: date, rules: Rules, *, open_draft: dict[str, Any] | None = None,
                 max_tasks: int = 10, max_candidates: int = 10) -> GenInputs:
    start, end = _day_bounds(day)
    existing = await storage.list_schedule_range(pool, start=start, end=end)

    now = datetime.now(IST)
    tasks = [t for t in await storage.list_pending_with_slips(pool)
             if t["due_at"] is not None and t["due_at"].astimezone(IST).date() <= day
             and stale_reason(t["due_at"], t["slip_count"], now) is None]      # stale ones are about to be dropped: not offered
    tasks.sort(key=lambda t: t["due_at"])

    instructions = [r["text"] for r in await storage.list_instruction_records(pool) if _in_range(r, day)]
    candidates = sorted((c for c in await storage.list_candidate_items(pool) if _in_range(c, day) and c["confidence"] >= 0.5),
                        key=lambda c: -c["confidence"])

    template_day, template = await find_template(pool, day)

    locked, current = [], []
    for raw in (open_draft or {}).get("entries", []):
        entry = DraftEntry.from_json(raw)
        if entry.state == PENDING:
            (locked if entry.locked else current).append(entry)

    fixed = [Block(r["start_at"].astimezone(IST), r["end_at"].astimezone(IST), r["title"]) for r in existing]
    fixed += [Block(e.start_at, e.end_at, e.title) for e in locked]

    return GenInputs(
        rules=rules, existing=existing, template_day=template_day, template=template, tasks=tasks[:max_tasks],
        instructions=instructions, candidates=candidates[:max_candidates], locked=locked, current=current,
        fixed=fixed, free=free_slots(rules, fixed),
    )
