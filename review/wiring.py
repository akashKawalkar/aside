# review/wiring.py — connects the pure ReviewGenerator to the database. Shared by the laptop server and the nightly job.
from __future__ import annotations

from review.generate import ReviewGenerator
from storage import count_notes, list_completed_tasks, list_schedule_range, list_sessions_range, list_tasks_due


def make_review_generator(pool) -> ReviewGenerator:
    return ReviewGenerator(
        fetch_sessions=lambda s, e: list_sessions_range(pool, start=s, end=e),
        fetch_schedule=lambda s, e: list_schedule_range(pool, start=s, end=e),
        fetch_completed_tasks=lambda s, e: list_completed_tasks(pool, start=s, end=e),
        fetch_notes_count=lambda s, e: count_notes(pool, start=s, end=e),
        fetch_due_tasks=lambda s, e: list_tasks_due(pool, start=s, end=e),
    )
