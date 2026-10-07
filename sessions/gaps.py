# sessions/gaps.py — where tracking has holes, and why. Pure functions: rows in, a month's picture out.
# A gap is a stretch inside waking hours (IST) with no session, long enough to matter. Idle counts as tracked.
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config import DataQuality

IST = ZoneInfo("Asia/Kolkata")
Interval = tuple[datetime, datetime]

PAUSED = "paused"
MACHINE_OFF = "computer off or asleep"
COLLECTOR_DOWN = "collector not running"
INGESTOR_DOWN = "ingestor not running"
UNEXPLAINED = "unexplained"


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for start, end in sorted(i for i in intervals if i[1] > i[0]):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def clip(intervals: list[Interval], window: Interval) -> list[Interval]:
    return [(max(s, window[0]), min(e, window[1])) for s, e in intervals if min(e, window[1]) > max(s, window[0])]


def complement(covered: list[Interval], window: Interval) -> list[Interval]:
    """The parts of `window` not covered by the (merged, clipped) intervals."""
    free, cursor = [], window[0]
    for start, end in covered:
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < window[1]:
        free.append((cursor, window[1]))
    return free


def overlap_seconds(a: Interval, intervals: list[Interval]) -> float:
    return sum(max((min(a[1], e) - max(a[0], s)).total_seconds(), 0.0) for s, e in intervals)


def pause_intervals(events: list[dict[str, Any]], until: datetime) -> list[Interval]:
    """Paused stretches from the pause/resume log (ascending). A pause with no resume lasts until `until`."""
    out, paused_at = [], None
    for event in events:
        if not event["enabled"] and paused_at is None:
            paused_at = event["at"]
        elif event["enabled"] and paused_at is not None:
            out.append((paused_at, event["at"]))
            paused_at = None
    if paused_at is not None:
        out.append((paused_at, until))
    return out


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    reason: str

    @property
    def minutes(self) -> int:
        return round((self.end - self.start).total_seconds() / 60)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "minutes": self.minutes, "reason": self.reason}


def explain(gap: Interval, pauses: list[Interval], heartbeats: list[dict[str, Any]], sample_interval: float) -> str:
    length = (gap[1] - gap[0]).total_seconds()
    if overlap_seconds(gap, pauses) >= length / 2:
        return PAUSED
    if not heartbeats or gap[0] < heartbeats[0]["ts"]:
        return UNEXPLAINED   # heartbeats were not being recorded yet, so nothing can be said
    samples = [h for h in heartbeats if gap[0] <= h["ts"] < gap[1]]
    if len(samples) * sample_interval < length / 2:
        return MACHINE_OFF   # the server itself was not sampling
    if sum(1 for h in samples if not h["collector_ok"]) * 2 >= len(samples):
        return COLLECTOR_DOWN
    if sum(1 for h in samples if not h["ingestor_ok"]) * 2 >= len(samples):
        return INGESTOR_DOWN
    return UNEXPLAINED


def month_picture(
    year: int,
    month: int,
    *,
    sessions: list[dict[str, Any]],
    monitoring: list[dict[str, Any]],
    heartbeats: list[dict[str, Any]],
    now: datetime,
    cfg: DataQuality,
    first_data_at: datetime | None,
) -> dict[str, Any]:
    """Per-day tracked time (for the heatmap) and the gap list with reasons, for one calendar month."""
    now = now.astimezone(IST)
    all_sessions = merge([(s["started_at"].astimezone(IST), s["ended_at"].astimezone(IST)) for s in sessions])
    pauses = pause_intervals(monitoring, now)
    wake_start, wake_end = (time(*map(int, t.split(":"))) for t in (cfg.waking_start, cfg.waking_end))
    min_gap = timedelta(minutes=cfg.min_gap_minutes)

    days, gaps = [], []
    for n in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date(year, month, n)
        midnight = datetime.combine(day, time.min, tzinfo=IST)
        tracked = sum((e - s).total_seconds() for s, e in clip(all_sessions, (midnight, midnight + timedelta(days=1))))

        # Gaps are only meaningful once tracking had started, and never in the future.
        lo = datetime.combine(day, wake_start, tzinfo=IST)
        hi = min(datetime.combine(day, wake_end, tzinfo=IST), now)
        if first_data_at is not None:
            lo = max(lo, first_data_at.astimezone(IST))
        day_gaps: list[Gap] = []
        if first_data_at is not None and hi > lo:
            for window in complement(clip(all_sessions, (lo, hi)), (lo, hi)):
                if window[1] - window[0] >= min_gap:
                    day_gaps.append(Gap(*window, explain(window, pauses, heartbeats, cfg.heartbeat_sample_interval)))

        gaps.extend(day_gaps)
        days.append({
            "date": day.isoformat(),
            "tracked_seconds": round(tracked),
            "gap_count": len(day_gaps),
            "gap_minutes": sum(g.minutes for g in day_gaps),
            "in_future": midnight > now,
        })

    return {"month": f"{year:04d}-{month:02d}", "days": days, "gaps": [g.to_dict() for g in gaps]}
