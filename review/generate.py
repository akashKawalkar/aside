from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

Fetcher = Callable[[datetime, datetime], Awaitable[Any]]

DEFAULT_TIMEZONE = "Asia/Kolkata"


@dataclass(frozen=True)
class DailyReview:
    """
    One finished review.

    data:
        The structured review (JSON-safe), which the settings page draws.

    text:
        The same review as plain text, for email.
    """

    review_date: date
    text: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    minutes = int(round(abs(seconds) / 60))
    hours, minutes = divmod(minutes, 60)

    if hours and minutes:
        return f"{hours}h {minutes}m"

    return f"{hours}h" if hours else f"{minutes}m"


def format_signed(seconds: float) -> str:
    if round(seconds / 60) == 0:
        return "on plan"

    return ("+" if seconds > 0 else "-") + format_duration(seconds)


def _clock(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def _app_name(app: str | None) -> str:
    name = (app or "Unknown").strip() or "Unknown"

    return name[:-4] if name.lower().endswith(".exe") else name


# ---------------------------------------------------------------------------
# Pure building blocks
# ---------------------------------------------------------------------------


def _clip(start: datetime, end: datetime, lo: datetime, hi: datetime):
    start, end = max(start, lo), min(end, hi)

    return (start, end) if end > start else None


def _union_seconds(spans: list[tuple[datetime, datetime]]) -> float:
    """Total length of the spans, counting overlaps once."""
    total = 0.0
    current_end: datetime | None = None

    for start, end in sorted(spans):
        if current_end is None or start >= current_end:
            total += (end - start).total_seconds()
            current_end = end
        elif end > current_end:
            total += (end - current_end).total_seconds()
            current_end = end

    return total


def build_day_data(
    *,
    day: date,
    window: tuple[datetime, datetime],
    sessions: list[dict],
    schedule: list[dict],
    completed: list[dict],
    notes: int,
    tomorrow_schedule: list[dict],
    tomorrow_tasks: list[dict],
    tz: ZoneInfo,
) -> dict[str, Any]:
    """
    Turn one day's raw rows into the review structure.

    Idle sessions are left out. Wall-clock time is the length of the
    app sessions themselves, not the sum of their active seconds.
    """
    lo, hi = window

    actual: list[tuple[datetime, datetime, str]] = []
    for session in sessions:
        if session.get("kind") != "app":
            continue

        span = _clip(session["started_at"], session["ended_at"], lo, hi)
        if span:
            actual.append((*span, _app_name(session.get("app"))))

    planned: list[tuple[datetime, datetime, str]] = []
    for entry in schedule:
        span = _clip(entry["start_at"], entry["end_at"], lo, hi)
        if span:
            planned.append((*span, entry["title"]))

    wall = _union_seconds([(s, e) for s, e, _ in actual])
    plan = _union_seconds([(s, e) for s, e, _ in planned])

    timeline = [
        {
            "kind": kind,
            "start": start.astimezone(tz).isoformat(),
            "end": end.astimezone(tz).isoformat(),
            "label": label,
        }
        for kind, rows in (("planned", planned), ("actual", actual))
        for start, end, label in rows
    ]
    timeline.sort(key=lambda row: (row["start"], row["kind"]))

    return {
        "scope": "day",
        "date": day.isoformat(),
        "quiet": not (actual or planned or completed or notes),
        "stats": {
            "wall_clock_seconds": round(wall),
            "planned_seconds": round(plan),
            # Only meaningful when there was a plan to compare against.
            "difference_seconds": round(wall - plan) if plan else None,
            "tasks_completed": len(completed),
            "notes": notes,
        },
        "completed_tasks": [task["text"] for task in completed if task["text"]],
        "timeline": timeline,
        "tomorrow": {
            "schedule": [
                {
                    "title": entry["title"],
                    "start": entry["start_at"].astimezone(tz).isoformat(),
                    "end": entry["end_at"].astimezone(tz).isoformat(),
                }
                for entry in tomorrow_schedule
            ],
            "tasks": [
                {"text": task["text"], "due": task["due_at"].astimezone(tz).isoformat()}
                for task in tomorrow_tasks
                if task["text"]
            ],
        },
    }


def build_period_data(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll finished day structures up into a weekly or monthly summary."""
    stats = [day["stats"] for day in days]
    planned = sum(s["planned_seconds"] for s in stats)
    wall = sum(s["wall_clock_seconds"] for s in stats)

    return {
        "scope": "period",
        "from": days[0]["date"],
        "to": days[-1]["date"],
        "quiet": all(day["quiet"] for day in days),
        "stats": {
            "wall_clock_seconds": wall,
            "planned_seconds": planned,
            "difference_seconds": wall - planned if planned else None,
            "tasks_completed": sum(s["tasks_completed"] for s in stats),
            "notes": sum(s["notes"] for s in stats),
        },
        "days": [
            {
                "date": day["date"],
                "wall_clock_seconds": day["stats"]["wall_clock_seconds"],
                "tasks_completed": day["stats"]["tasks_completed"],
            }
            for day in days
        ],
    }


def _stat_lines(stats: dict[str, Any]) -> list[str]:
    lines = []

    if stats["wall_clock_seconds"]:
        lines.append(f"Wall-clock time: {format_duration(stats['wall_clock_seconds'])}")

    if stats["difference_seconds"] is not None:
        lines.append(
            f"Plan: {format_duration(stats['planned_seconds'])}"
            f" ({format_signed(stats['difference_seconds'])})"
        )

    if stats["tasks_completed"]:
        lines.append(f"Tasks completed: {stats['tasks_completed']}")

    if stats["notes"]:
        lines.append(f"Notes: {stats['notes']}")

    return lines


def render_text(data: dict[str, Any]) -> str:
    """Plain-text form of a day or period structure."""
    if data["scope"] == "period":
        out = [f"Review — {data['from']} to {data['to']}", ""]

        out += _stat_lines(data["stats"]) or ["Quiet period. Nothing recorded."]

        busy = [day for day in data["days"] if day["wall_clock_seconds"]]
        if busy:
            out += ["", "By day"]
            out += [
                f"  {day['date']}  {format_duration(day['wall_clock_seconds'])}"
                for day in busy
            ]

        return "\n".join(out)

    out = [f"Review — {data['date']}", ""]

    if data["quiet"]:
        out.append("Quiet day. Nothing recorded.")
    else:
        out += _stat_lines(data["stats"])

    if data["completed_tasks"]:
        out += ["", "Done"] + [f"  - {text}" for text in data["completed_tasks"]]

    if data["timeline"]:
        out += ["", "Timeline"]
        for row in data["timeline"]:
            start = datetime.fromisoformat(row["start"])
            end = datetime.fromisoformat(row["end"])
            out.append(
                f"  {_clock(start)} - {_clock(end)}  "
                f"{row['kind'].capitalize()}: {row['label']}"
            )

    tomorrow = data["tomorrow"]
    if tomorrow["schedule"] or tomorrow["tasks"]:
        out += ["", "Tomorrow"]
        for entry in tomorrow["schedule"]:
            start = datetime.fromisoformat(entry["start"])
            end = datetime.fromisoformat(entry["end"])
            out.append(f"  {_clock(start)} - {_clock(end)}  {entry['title']}")
        for task in tomorrow["tasks"]:
            due = datetime.fromisoformat(task["due"])
            out.append(f"  Due {_clock(due)}: {task['text']}")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class ReviewGenerator:
    """
    Builds reviews from injected fetchers; it never touches Postgres,
    sends mail, or calls a model.

    A fetcher that fails, or is missing, is treated as "no data" for that
    source, so one broken source never blocks the review.
    """

    def __init__(
        self,
        *,
        fetch_sessions: Fetcher | None = None,
        fetch_schedule: Fetcher | None = None,
        fetch_completed_tasks: Fetcher | None = None,
        fetch_notes_count: Fetcher | None = None,
        fetch_due_tasks: Fetcher | None = None,
        timezone_name: str = DEFAULT_TIMEZONE,
    ) -> None:
        self.fetch_sessions = fetch_sessions
        self.fetch_schedule = fetch_schedule
        self.fetch_completed_tasks = fetch_completed_tasks
        self.fetch_notes_count = fetch_notes_count
        self.fetch_due_tasks = fetch_due_tasks
        self.timezone = ZoneInfo(timezone_name)

    def today(self) -> date:
        return datetime.now(self.timezone).date()

    def day_window(self, day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day, time.min, tzinfo=self.timezone)

        return start, start + timedelta(days=1)

    async def day_data(self, day: date) -> dict[str, Any]:
        window = self.day_window(day)
        tomorrow = self.day_window(day + timedelta(days=1))

        sessions, schedule, completed, notes, tomorrow_schedule, tomorrow_tasks = (
            await asyncio.gather(
                self._fetch(self.fetch_sessions, window, [], "sessions"),
                self._fetch(self.fetch_schedule, window, [], "schedule"),
                self._fetch(self.fetch_completed_tasks, window, [], "completed tasks"),
                self._fetch(self.fetch_notes_count, window, 0, "notes"),
                self._fetch(self.fetch_schedule, tomorrow, [], "tomorrow's schedule"),
                self._fetch(self.fetch_due_tasks, tomorrow, [], "tomorrow's tasks"),
            )
        )

        return build_day_data(
            day=day,
            window=window,
            sessions=sessions,
            schedule=schedule,
            completed=completed,
            notes=notes,
            tomorrow_schedule=tomorrow_schedule,
            tomorrow_tasks=tomorrow_tasks,
            tz=self.timezone,
        )

    async def generate(self, *, review_date: date | None = None) -> DailyReview:
        day = review_date or self.today()
        data = await self.day_data(day)

        return DailyReview(review_date=day, text=render_text(data), data=data)

    async def generate_period(self, *, first: date, last: date) -> DailyReview:
        """Weekly or monthly summary covering first..last inclusive."""
        days, day = [], first

        while day <= last:
            days.append(await self.day_data(day))
            day += timedelta(days=1)

        data = build_period_data(days)

        return DailyReview(review_date=last, text=render_text(data), data=data)

    async def _fetch(self, fetcher, window, default, name: str):
        if fetcher is None:
            return default

        try:
            return await fetcher(*window)
        except Exception:
            log.exception("review: %s unavailable", name)
            return default


async def generate(
    *,
    review_date: date | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    **fetchers: Fetcher | None,
) -> DailyReview:
    """Public H.1 entrypoint."""
    return await ReviewGenerator(
        timezone_name=timezone_name, **fetchers
    ).generate(review_date=review_date)
