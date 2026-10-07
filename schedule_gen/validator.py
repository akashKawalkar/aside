# schedule_gen/validator.py — code checks every proposed block; the model's output is never trusted as-is.
# Rules (plan 3.6 step 3): inside the target day and the waking window (IST), a sane length, no overlap with anything
# already fixed, and no overlap between the proposals themselves. Whatever fails is returned with a reason, not dropped silently.
from __future__ import annotations

from typing import Any

from schedule_gen.model import IST, MAX_TITLE_LENGTH, Block, DraftEntry, Rules, span


def check(entry: DraftEntry, rules: Rules, fixed: list[Block]) -> str | None:
    """Why this single entry is invalid, or None. Does not look at the other proposals."""
    title = (entry.title or "").strip()
    if not title:
        return "empty_title"
    if len(title) > MAX_TITLE_LENGTH:
        return "title_too_long"
    if entry.start_at.tzinfo is None or entry.end_at.tzinfo is None:
        return "bad_times"
    start, end = entry.start_at.astimezone(IST), entry.end_at.astimezone(IST)
    if end <= start:
        return "bad_times"
    if start.date() != rules.day or end.date() != rules.day:
        return "wrong_day"
    window_start, window_end = rules.window()
    if start < window_start or end > window_end:
        return "outside_waking_hours"
    minutes = (end - start).total_seconds() / 60
    if minutes < rules.min_minutes:
        return "too_short"
    if minutes > rules.max_minutes:
        return "too_long"
    for block in fixed:
        if block.overlaps(start, end):
            return f"overlaps_fixed:{block.title or span(block.start_at, block.end_at)}"
    return None


def validate(entries: list[DraftEntry], rules: Rules, fixed: list[Block] = ()) -> tuple[list[DraftEntry], list[dict[str, Any]]]:
    """(valid entries in time order, [{entry, reason}] for the rest). Of two overlapping proposals the earlier one wins."""
    valid: list[DraftEntry] = []
    rejected: list[dict[str, Any]] = []

    for entry in sorted(entries, key=lambda e: (e.start_at, e.end_at)):
        reason = check(entry, rules, list(fixed))
        if reason is None:
            clash = next((v for v in valid if v.start_at < entry.end_at and v.end_at > entry.start_at), None)
            if clash:
                reason = f"overlaps_draft:{clash.title}"
        if reason is None and len(valid) >= rules.max_entries:
            reason = "too_many"
        if reason is None:
            entry.title = entry.title.strip()
            entry.start_at, entry.end_at = entry.start_at.astimezone(IST), entry.end_at.astimezone(IST)
            valid.append(entry)
        else:
            rejected.append({"entry": entry.to_json(), "reason": reason})

    return valid, rejected
