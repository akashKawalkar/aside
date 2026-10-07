# schedule_gen/compare.py — the learning signal of plan 3.6: the FIRST generated draft against the day's FINAL schedule.
# Pure. Edits in between (schedule_log) deliberately play no part (user decision 2026-10-07).
from __future__ import annotations

from datetime import datetime
from typing import Any

from schedule_gen.model import IST


def _t(value: Any) -> datetime:
    return (datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone(IST)


def draft_vs_final(draft: list[dict[str, Any]], final: list[dict[str, Any]]) -> dict[str, Any]:
    """Match by title (case-insensitive). `kept` = same time, `moved` = same title at another time, `dropped` = in the
    draft but not in the final schedule, `added` = in the final schedule but never drafted (with who made it)."""
    unmatched = list(final)
    kept, moved, dropped = [], [], []
    for d in draft:
        match = next((f for f in unmatched if f["title"].casefold() == d["title"].casefold()), None)
        if match is None:
            dropped.append({"title": d["title"], "start_at": _t(d["start_at"]).isoformat()})
            continue
        unmatched.remove(match)
        if _t(d["start_at"]) == _t(match["start_at"]) and _t(d["end_at"]) == _t(match["end_at"]):
            kept.append({"title": d["title"]})
        else:
            moved.append({"title": d["title"], "from": _t(d["start_at"]).isoformat(), "to": _t(match["start_at"]).isoformat()})
    added = [{"title": f["title"], "origin": f.get("origin", "user")} for f in unmatched]
    return {"kept": kept, "moved": moved, "dropped": dropped, "added": added, "draft_size": len(draft), "final_size": len(final)}
