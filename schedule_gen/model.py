# schedule_gen/model.py — the draft entry and the rules a draft is checked against. Pure; no database, no model calls.
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DURATION = timedelta(minutes=30)       # plan 3.6: 30 minutes when no end is given
MAX_TITLE_LENGTH = 200                         # same as knowledge.schedule.MAX_TITLE_LENGTH (a test pins them together)

PENDING, ACCEPTED, DISCARDED = "pending", "accepted", "discarded"


def tomorrow(now: datetime) -> date:
    """Generation is for tomorrow only (plan 3.6), by the IST calendar."""
    return now.astimezone(IST).date() + timedelta(days=1)


def hhmm(moment: datetime) -> str:
    return f"{moment.astimezone(IST):%H:%M}"


def span(start: datetime, end: datetime) -> str:
    return f"{hhmm(start)}-{hhmm(end)}"


@dataclass
class DraftEntry:
    """One proposed block. `locked` entries are not regenerated: the user edited or accepted them."""
    title: str
    start_at: datetime
    end_at: datetime
    reason: str = ""
    state: str = PENDING                  # pending | accepted | discarded
    locked: bool = False
    edited: bool = False                  # the user changed it inside the draft
    schedule_id: int | None = None        # set once accepted: the schedule row it became

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_at"], d["end_at"] = self.start_at.isoformat(), self.end_at.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> DraftEntry:
        return cls(
            title=d["title"],
            start_at=datetime.fromisoformat(d["start_at"]).astimezone(IST),
            end_at=datetime.fromisoformat(d["end_at"]).astimezone(IST),
            reason=d.get("reason", ""),
            state=d.get("state", PENDING),
            locked=bool(d.get("locked", False)),
            edited=bool(d.get("edited", False)),
            schedule_id=d.get("schedule_id"),
        )


@dataclass(frozen=True)
class Block:
    """A stretch of the day that is already taken and must not be overlapped."""
    start_at: datetime
    end_at: datetime
    title: str = ""

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return start < self.end_at and end > self.start_at     # touching (end == start) is not an overlap


@dataclass(frozen=True)
class Rules:
    """What a valid draft looks like. Times are IST; the waking window comes from config `[data_quality]`."""
    day: date
    waking_start: time = time(7, 0)
    waking_end: time = time(23, 0)
    min_minutes: int = 15
    max_minutes: int = 12 * 60
    max_entries: int = 20

    def window(self) -> tuple[datetime, datetime]:
        return (datetime.combine(self.day, self.waking_start, IST), datetime.combine(self.day, self.waking_end, IST))


def rules_from_config(day: date, data_quality) -> Rules:
    start, end = (time(*map(int, t.split(":"))) for t in (data_quality.waking_start, data_quality.waking_end))
    return Rules(day=day, waking_start=start, waking_end=end)


def settled_status(entries: list[DraftEntry]) -> str | None:
    """None while anything is still pending; otherwise `accepted` if at least one entry became a schedule row, else `discarded`."""
    if any(e.state == PENDING for e in entries):
        return None
    return "accepted" if any(e.state == ACCEPTED for e in entries) else "discarded"


def free_slots(rules: Rules, busy: list[Block], min_minutes: int = 30) -> list[tuple[datetime, datetime]]:
    """The gaps inside the waking window that no block occupies, at least `min_minutes` long."""
    start, end = rules.window()
    cursor, gaps = start, []
    for block in sorted(busy, key=lambda b: b.start_at):
        if block.end_at <= cursor:
            continue
        if block.start_at > cursor and block.start_at < end:
            gaps.append((cursor, min(block.start_at, end)))
        cursor = max(cursor, block.end_at)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append((cursor, end))
    return [(a, b) for a, b in gaps if b - a >= timedelta(minutes=min_minutes)]
