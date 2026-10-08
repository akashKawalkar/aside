# schedule_gen/parse.py — turn the model's reply into draft entries. Tolerant of code fences and prose around the JSON,
# strict about the shape. Times are "HH:MM" in IST on the target day (far harder to get wrong than ISO offsets).
from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from typing import Any

from schedule_gen.model import DEFAULT_DURATION, IST, DraftEntry

TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


class ParseError(ValueError):
    """The reply was not usable at all (no JSON object, wrong shape). The call is already spent, so say why."""


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")      # prose before/after the object
    if start == -1 or end <= start:
        raise ParseError("the reply contained no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ParseError(f"the reply was not valid JSON ({exc.msg})") from None


def _clock(value: Any, day: date) -> datetime | None:
    if isinstance(value, str):
        m = TIME_RE.match(value)
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            return datetime.combine(day, time(int(m.group(1)), int(m.group(2))), IST)
        try:                                           # an ISO datetime is accepted too
            moment = datetime.fromisoformat(value)
            return moment.astimezone(IST) if moment.tzinfo else moment.replace(tzinfo=IST)
        except ValueError:
            return None
    return None


def parse_entries(text: str, day: date) -> tuple[list[DraftEntry], list[dict[str, Any]]]:
    """(entries, rejected). Items that cannot be read at all go to `rejected` with reason `unreadable_entry`;
    an empty list is a legitimate answer ("nothing worth blocking")."""
    data = _extract_json(text)
    items = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ParseError('expected {"entries": [...]}')

    entries: list[DraftEntry] = []
    rejected: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            rejected.append({"entry": {"raw": str(item)[:200]}, "reason": "unreadable_entry"})
            continue
        start = _clock(item.get("start"), day)
        end = _clock(item.get("end"), day) if item.get("end") not in (None, "") else (start + DEFAULT_DURATION if start else None)
        title = item.get("title")
        if not isinstance(title, str) or start is None or end is None:
            rejected.append({"entry": {k: str(v)[:200] for k, v in item.items()}, "reason": "unreadable_entry"})
            continue
        reason = item.get("reason")
        task_id = item.get("task_id")
        task_id = task_id if isinstance(task_id, int) and not isinstance(task_id, bool) else None
        entries.append(DraftEntry(title=title, start_at=start, end_at=end, reason=reason.strip() if isinstance(reason, str) else "",
                                  task_id=task_id))
    return entries, rejected
