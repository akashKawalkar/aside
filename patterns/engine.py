"""Turn recorded statistics into small, auditable observations."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config import Patterns
from patterns.stats import (
    focus_hour_distribution,
    low_intentional_activity_days,
    routine_summary,
    task_slip_counts,
)
from storage import list_day_records, list_observations, list_task_due_changes, upsert_observation

IST = ZoneInfo("Asia/Kolkata")


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return None


def _bucket(day: date) -> str:
    return "weekend" if day.weekday() >= 5 else "weekday"


def _status(occurrences: int, misses: int, cfg: Patterns) -> str:
    if misses >= cfg.drop_after_misses:
        return "dropped"
    if misses >= cfg.question_after_misses:
        return "questioned"
    return "active" if occurrences >= cfg.min_occurrences else "candidate"


def derive_observations(
    day_records: list[dict[str, Any]],
    due_changes: list[dict[str, Any]],
    *,
    cfg: Patterns,
) -> list[dict[str, Any]]:
    """Build observation rows from a bounded set of completed daily records."""
    records = [r for r in day_records if not r.get("late", False) and _day(r.get("day"))]
    records.sort(key=lambda r: _day(r["day"]))
    if not records:
        return []
    as_of = _day(records[-1]["day"])
    result: dict[str, dict[str, Any]] = {}

    summaries = routine_summary(records)
    for bucket, summary in summaries.items():
        for routine in summary["routines"]:
            title = routine["title"]
            occurrence_days = routine["occurrence_days"]
            latest = date.fromisoformat(occurrence_days[-1])
            misses = 0
            for record in reversed(records):
                day = _day(record["day"])
                if day is None or day <= latest:
                    break
                if _bucket(day) != bucket:
                    continue
                current = routine_summary([record])[bucket]["routines"]
                if any(item["title"].casefold() == title.casefold() for item in current):
                    break
                misses += 1
            digest = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:24]
            key = f"routine:{bucket}:{digest}"
            active = routine["occurrences"] >= cfg.min_occurrences and misses < cfg.question_after_misses
            result[key] = {
                "observation_key": key,
                "kind": f"routine_{bucket}",
                "text": (f"Your schedule often includes {title} on {bucket}s." if active
                         else f"Your schedule includes {title} on {bucket}s."),
                "status": _status(routine["occurrences"], misses, cfg),
                "occurrences": routine["occurrences"],
                "misses": misses,
                "first_seen": date.fromisoformat(occurrence_days[0]),
                "last_seen": latest,
                "last_evaluated_day": as_of,
                "evidence": {"occurrence_days": occurrence_days, "bucket": bucket, "title": title,
                             "median_start_minute": routine["start_minute_median"],
                             "median_duration_minutes": routine["duration_minutes_median"]},
            }

    focus = focus_hour_distribution(records)
    for hour, seconds in enumerate(focus["seconds_by_hour"]):
        days = focus["days_by_hour"][hour]
        if seconds < cfg.focus_hour_min_seconds or not days:
            continue
        key = f"focus_hour:{hour:02d}"
        active = len(days) >= cfg.min_occurrences
        result[key] = {
            "observation_key": key,
            "kind": "focus_hour",
            "text": (f"You often record activity around {hour:02d}:00 IST." if active
                     else f"Activity was recorded around {hour:02d}:00 IST."),
            "status": _status(len(days), 0, cfg),
            "occurrences": len(days),
            "misses": 0,
            "first_seen": date.fromisoformat(days[0]),
            "last_seen": date.fromisoformat(days[-1]),
            "last_evaluated_day": as_of,
            "evidence": {"days": days, "seconds": seconds, "hour": hour},
        }

    low_days = low_intentional_activity_days(records, minimum_seconds=cfg.low_intentional_seconds)
    if low_days:
        occurrence_days = [row["day"] for row in low_days]
        key = "low_scheduled_overlap"
        misses = 0
        for record in reversed(records):
            day = _day(record["day"])
            if day is None or day <= date.fromisoformat(occurrence_days[-1]):
                break
            misses += 1
        result[key] = {
            "observation_key": key,
            "kind": "low_intentional_activity",
            "text": ("On several recorded days, little app activity overlapped the schedule."
                     if len(occurrence_days) >= cfg.min_occurrences
                     else "Little app activity overlapped the schedule on a recorded day."),
            "status": _status(len(occurrence_days), misses, cfg),
            "occurrences": len(occurrence_days),
            "misses": misses,
            "first_seen": date.fromisoformat(occurrence_days[0]),
            "last_seen": date.fromisoformat(occurrence_days[-1]),
            "last_evaluated_day": as_of,
            "evidence": {"days": occurrence_days},
        }

    since = as_of - timedelta(days=cfg.lookback_days - 1)
    for task in task_slip_counts(due_changes):
        days = task["occurrence_days"]
        if task["occurrences"] < 1:
            continue
        title = task.get("text") or f"Task {task['task_id']}"
        key = f"task_slips:{task['task_id']}"
        active = task["occurrences"] >= cfg.min_occurrences
        result[key] = {
            "observation_key": key,
            "kind": "task_slips",
            "text": (f"The due date for {title!r} was moved later {task['slips']} times."
                     if active else f"The due date for {title!r} was moved later."),
            "status": _status(task["occurrences"], 0, cfg),
            "occurrences": task["occurrences"],
            "misses": 0,
            "first_seen": date.fromisoformat(days[0]) if days else since,
            "last_seen": date.fromisoformat(days[-1]) if days else as_of,
            "last_evaluated_day": as_of,
            "evidence": {"task_id": task["task_id"], "slips": task["slips"], "days": days},
        }
    return list(result.values())


async def run_patterns_once(pool, cfg: Patterns, *, now: datetime | None = None) -> int:
    """Refresh observations once from settled day records; repeated runs are idempotent."""
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    first = today - timedelta(days=cfg.lookback_days - 1)
    records = await list_day_records(pool, first=first, last=today)
    if not records:
        return 0
    latest_day = _day(records[-1]["day"])
    if latest_day is None:
        return 0
    since = datetime.combine(first, datetime.min.time(), tzinfo=IST)
    changes = await list_task_due_changes(pool, since=since)
    candidates = {row["observation_key"]: row for row in derive_observations(records, changes, cfg=cfg)}
    existing = await list_observations(pool)
    for row in existing:
        key = row["observation_key"]
        if key in candidates:
            continue
        evaluated = _day(row.get("last_evaluated_day"))
        if evaluated is not None and evaluated >= latest_day:
            continue
        missed_records = [record for record in records if (evaluated is None or _day(record["day"]) > evaluated)]
        misses = int(row.get("misses") or 0) + len(missed_records)
        updated = dict(row)
        updated["misses"] = misses
        updated["status"] = _status(int(row.get("occurrences") or 0), misses, cfg)
        updated["last_evaluated_day"] = latest_day
        candidates[key] = updated
    for row in candidates.values():
        await upsert_observation(pool, row)
    return len(candidates)
