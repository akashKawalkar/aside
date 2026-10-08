"""Deterministic summaries over the existing nightly day-record shape.

No inference beyond recorded schedule entries, application sessions, and task
due-date edits happens here. In particular, schedule overlap is a lower bound
on intentional activity; it is not a judgement about uncovered time.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.astimezone(IST)


def _day_value(record: dict[str, Any]) -> date | None:
    value = record.get("day") or record.get("date")
    if isinstance(value, datetime):
        return value.astimezone(IST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _record_data(record: dict[str, Any]) -> dict[str, Any]:
    data = record.get("data", record)
    return data if isinstance(data, dict) else {}


def _schedule(record: dict[str, Any]) -> list[dict[str, Any]]:
    entries = _record_data(record).get("schedule", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _actual_spans(record: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    review = _record_data(record).get("review", {})
    timeline = review.get("timeline", []) if isinstance(review, dict) else []
    spans = []
    for row in timeline if isinstance(timeline, list) else []:
        if not isinstance(row, dict) or row.get("kind") != "actual":
            continue
        start, end = _parse_datetime(row.get("start")), _parse_datetime(row.get("end"))
        if start and end and end > start:
            spans.append((start, end))
    return _merge(spans)


def _interval(entry: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start, end = _parse_datetime(entry.get("start_at") or entry.get("start")), _parse_datetime(
        entry.get("end_at") or entry.get("end")
    )
    return (start, end) if start and end and end > start else None


def _merge(spans: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[list[datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _seconds(spans: Iterable[tuple[datetime, datetime]]) -> int:
    return round(sum((end - start).total_seconds() for start, end in spans))


def _title(entry: dict[str, Any]) -> str:
    return " ".join(str(entry.get("title") or "").split())


def _user_backed(entry: dict[str, Any]) -> bool:
    """Generated blocks count only after the user has edited them."""
    return entry.get("origin", "user") == "user" or bool(entry.get("edited_by_user"))


def routine_summary(day_records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize repeated user-backed schedule blocks by weekday/weekend.

    Titles are grouped only by case and whitespace. There is no synonym
    guessing, and each bucket reports its sample size for downstream gating.
    """
    groups: dict[str, dict[str, dict[str, Any]]] = {
        "weekday": defaultdict(dict), "weekend": defaultdict(dict)
    }
    active_days = {"weekday": set(), "weekend": set()}
    for record in day_records:
        day = _day_value(record)
        if day is None or record.get("late", False):
            continue
        bucket = "weekend" if day.weekday() >= 5 else "weekday"
        for entry in _schedule(record):
            title = _title(entry)
            interval = _interval(entry)
            if not title or not interval or not _user_backed(entry):
                continue
            start, end = interval
            if start.date() != day or end.date() != day:
                continue
            key = title.casefold()
            # Keep the user's original casing for display while matching case-insensitively.
            entry_stats = groups[bucket].setdefault(key, {"title": title, "times": [], "days": set()})
            entry_stats["times"].append((start.hour * 60 + start.minute, round((end - start).total_seconds() / 60)))
            entry_stats["days"].add(day.isoformat())
            active_days[bucket].add(day)

    result: dict[str, Any] = {}
    for bucket in ("weekday", "weekend"):
        routines = []
        for key, data in sorted(groups[bucket].items()):
            occurrences = data["times"]
            routines.append({
                "title": data["title"],
                "occurrences": len(data["days"]),
                "occurrence_days": sorted(data["days"]),
                "start_minute_median": round(median(start for start, _ in occurrences)),
                "duration_minutes_median": round(median(duration for _, duration in occurrences)),
            })
        result[bucket] = {"days_with_schedule": len(active_days[bucket]), "routines": routines}
    return result


def focus_hour_distribution(day_records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return application-session seconds by IST hour, deduplicated per day."""
    seconds = [0] * 24
    days_by_hour = [set() for _ in range(24)]
    days_seen = set()
    for record in day_records:
        day = _day_value(record)
        if day is None or record.get("late", False):
            continue
        spans = []
        for start, end in _actual_spans(record):
            lo = max(start, datetime.combine(day, time.min, tzinfo=IST))
            hi = min(end, datetime.combine(day + timedelta(days=1), time.min, tzinfo=IST))
            if hi > lo:
                spans.append((lo, hi))
        spans = _merge(spans)
        if spans:
            days_seen.add(day)
        for start, end in spans:
            cursor = start
            while cursor < end:
                next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                boundary = min(end, next_hour)
                seconds[cursor.hour] += round((boundary - cursor).total_seconds())
                days_by_hour[cursor.hour].add(day.isoformat())
                cursor = boundary
    return {
        "days_with_activity": len(days_seen),
        "seconds_by_hour": seconds,
        "days_by_hour": [sorted(days) for days in days_by_hour],
    }


def planned_vs_actual(day_records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare the final captured schedule with recorded app sessions per day.

    Only final user-backed schedule entries count. The overlap is a conservative
    measure of scheduled activity; time outside it is left unclassified.
    """
    output = []
    for record in sorted(day_records, key=lambda row: _day_value(row) or date.min):
        day = _day_value(record)
        if day is None or record.get("late", False):
            continue
        planned = []
        for entry in _schedule(record):
            interval = _interval(entry)
            if interval and _user_backed(entry):
                start, end = interval
                if start.date() == day and end.date() == day:
                    planned.append(interval)
        actual = _actual_spans(record)
        overlaps = []
        for p_start, p_end in planned:
            for a_start, a_end in actual:
                start, end = max(p_start, a_start), min(p_end, a_end)
                if end > start:
                    overlaps.append((start, end))
        output.append({
            "day": day.isoformat(),
            "planned_seconds": _seconds(_merge(planned)),
            "actual_seconds": _seconds(actual),
            "scheduled_actual_seconds": _seconds(_merge(overlaps)),
        })
    return output


def task_slip_counts(changes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count logged due-date moves later in time, grouped by task id.

    This is strictly a count of recorded postponements. It does not infer a
    slip when a task remains overdue or when its due date was removed.
    """
    counts: dict[int, dict[str, Any]] = {}
    for row in changes:
        task_id = row.get("task_id")
        if task_id is None:
            continue
        old, new = _parse_datetime(row.get("old_due")), _parse_datetime(row.get("new_due"))
        if not old or not new or new <= old:
            continue
        try:
            key = int(task_id)
        except (TypeError, ValueError):
            continue
        item = counts.setdefault(key, {"task_id": key, "text": row.get("text"), "slips": 0, "occurrence_days": set()})
        item["slips"] += 1
        changed = _parse_datetime(row.get("created_at"))
        if changed:
            item["occurrence_days"].add(changed.date().isoformat())
    results = []
    for item in counts.values():
        item["occurrence_days"] = sorted(item["occurrence_days"])
        item["occurrences"] = len(item["occurrence_days"]) or item["slips"]
        results.append(item)
    return sorted(results, key=lambda row: (-row["slips"], row["task_id"]))


def low_intentional_activity_days(
    day_records: Iterable[dict[str, Any]], *, minimum_seconds: int = 1800
) -> list[dict[str, Any]]:
    """Find days with little recorded overlap between user-backed plans and app sessions.

    The name describes the measured overlap only. It is never a quality score:
    unplanned work and task-related activity cannot be attributed by current data.
    """
    if minimum_seconds < 0:
        raise ValueError("minimum_seconds must be non-negative")
    results = []
    for stats in planned_vs_actual(day_records):
        if stats["planned_seconds"] and stats["scheduled_actual_seconds"] < minimum_seconds:
            results.append({
                "day": stats["day"],
                "scheduled_actual_seconds": stats["scheduled_actual_seconds"],
                "planned_seconds": stats["planned_seconds"],
            })
    return results
