#sessions/task_lifecycle.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class LifecycleAction(StrEnum):
    KEEP = "keep"
    COLLAPSE = "collapse"
    DELETE = "delete"


@dataclass(frozen=True)
class TaskState:
    """
    Minimal state required by the lifecycle policy.

    This is intentionally independent of the eventual database model.
    """

    id: int
    due_at: datetime
    created_at: datetime
    completed: bool


@dataclass(frozen=True)
class LifecycleDecision:
    task_id: int
    action: LifecycleAction
    reason: str


class TaskLifecycle:
    """
    Applies the fixed task lifecycle policy.

    Pure policy only. Deletion of expired tasks is executed in SQL by
    storage.repo.tasks.expire_tasks, which uses the same three-day window.

    """

    COLLAPSE_AFTER = timedelta(days=7)
    DELETE_AFTER_DUE = timedelta(days=3)

    def evaluate(
        self,
        task: TaskState,
        *,
        now: datetime,
    ) -> LifecycleDecision:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")

        if task.due_at.tzinfo is None or task.due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")

        if task.created_at.tzinfo is None or task.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

        if task.completed:
            return LifecycleDecision(
                task_id=task.id,
                action=LifecycleAction.KEEP,
                reason="completed",
            )

        # Past due: retain for exactly three days, then delete.
        if now > task.due_at:
            age_after_due = now - task.due_at

            if age_after_due >= self.DELETE_AFTER_DUE:
                return LifecycleDecision(
                    task_id=task.id,
                    action=LifecycleAction.DELETE,
                    reason="past_due_for_three_days",
                )

            return LifecycleDecision(
                task_id=task.id,
                action=LifecycleAction.KEEP,
                reason="past_due_within_three_day_window",
            )

        # Not yet due: old pending tasks collapse after one week.
        age = now - task.created_at

        if age > self.COLLAPSE_AFTER:
            return LifecycleDecision(
                task_id=task.id,
                action=LifecycleAction.COLLAPSE,
                reason="undone_and_older_than_one_week",
            )

        return LifecycleDecision(
            task_id=task.id,
            action=LifecycleAction.KEEP,
            reason="active",
        )


def evaluate_tasks(
    tasks: list[TaskState],
    *,
    now: datetime,
) -> list[LifecycleDecision]:
    """
    Evaluate a batch of tasks deterministically.
    """

    lifecycle = TaskLifecycle()

    return [
        lifecycle.evaluate(task, now=now)
        for task in tasks
    ]


STALE_AFTER = timedelta(days=4)
MAX_SLIPS = 2


def stale_reason(due_at: datetime | None, slip_count: int, now: datetime) -> str | None:
    """Why a pending task should be dropped (cloud plan section 2), or None. Dropped, not lingering."""
    if slip_count >= MAX_SLIPS:
        return f"postponed {slip_count} times"
    if due_at is not None and now - due_at > STALE_AFTER:
        return f"overdue by more than {STALE_AFTER.days} days"
    return None
