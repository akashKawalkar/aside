from __future__ import annotations

from datetime import datetime
from typing import Any
from datetime import datetime, timedelta
import json

CREATE_SQL = """
    INSERT INTO tasks (text, due_at)
    VALUES (%s, %s)
    RETURNING id, text, due_at, status, created_at, completed_at
"""


LIST_SQL = """
    SELECT id, text, due_at, status, created_at, completed_at
    FROM tasks
    WHERE status = %s
    ORDER BY
        due_at IS NULL,
        due_at ASC,
        created_at ASC
"""
UPDATE_SQL = """
    UPDATE tasks
    SET
        text = %s,
        due_at = %s
    WHERE id = %s
    RETURNING id, text, due_at, status, created_at, completed_at
"""

GET_SQL = """
    SELECT id, text, due_at, status, created_at, completed_at
    FROM tasks
    WHERE id = %s
"""


COMPLETE_SQL = """
    UPDATE tasks
    SET
        status = 'completed',
        completed_at = %s
    WHERE id = %s
      AND status != 'completed'
    RETURNING id, text, due_at, status, created_at, completed_at
"""


DELETE_SQL = """
    DELETE FROM tasks
    WHERE id = %s
    RETURNING id
"""
TASK_LOG_SQL = """
    INSERT INTO task_log (task_id, action, snapshot)
    VALUES (%s, %s, %s)
"""
DUE_LOG_SQL = """
    INSERT INTO task_due_log (task_id, old_due, new_due)
    VALUES (%s, %s, %s)
"""
EXPIRE_SQL = """
    SELECT id, text, due_at, status, created_at, completed_at
    FROM tasks
    WHERE status = 'pending'
      AND due_at IS NOT NULL
      AND due_at < %s
"""
TASK_LOG_LIST_SQL = """
    SELECT id, task_id, action, snapshot, created_at
    FROM task_log
    ORDER BY created_at DESC, id DESC
    LIMIT %s
"""


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "text": row[1],
        "due_at": row[2],
        "status": row[3],
        "created_at": row[4],
        "completed_at": row[5],
    }


async def create_task(
    pool,
    *,
    text: str,
    due_at: datetime | None = None,
) -> dict[str, Any]:
    text = text.strip()

    if due_at is None:
        due_at = datetime.now().astimezone() + timedelta(hours=24)

    if due_at.tzinfo is None or due_at.utcoffset() is None:
        raise ValueError("due_at must be timezone-aware")

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                CREATE_SQL,
                (text, due_at),
            )

            row = await cur.fetchone()

    return _row_to_dict(row)


async def list_tasks(
    pool,
    *,
    status: str = "pending",
) -> list[dict[str, Any]]:
    if not status:
        raise ValueError("status must not be empty")

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                LIST_SQL,
                (status,),
            )

            rows = await cur.fetchall()

    return [_row_to_dict(row) for row in rows]


async def get_task(
    pool,
    *,
    task_id: int,
) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                GET_SQL,
                (task_id,),
            )

            row = await cur.fetchone()

    if row is None:
        return None

    return _row_to_dict(row)

_UNSET = object()


async def update_task(
    pool,
    *,
    task_id: int,
    text: str | None = None,
    due_at: datetime | None | object = _UNSET,
) -> dict[str, Any] | None:
    if text is not None:
        text = text.strip()

        if not text:
            raise ValueError("task text must not be empty")

    if due_at is not _UNSET:
        if due_at is None:
            due_at = datetime.now().astimezone() + timedelta(hours=24)

        if due_at.tzinfo is None or due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")

    if text is None and due_at is _UNSET:
        raise ValueError("at least one task field must be provided")

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            old_due = None
            if due_at is not _UNSET:
                await cur.execute("SELECT due_at FROM tasks WHERE id = %s FOR UPDATE", (task_id,))
                found = await cur.fetchone()
                old_due = found[0] if found else None

            if text is not None and due_at is not _UNSET:
                await cur.execute(
                    UPDATE_SQL,
                    (
                        text,
                        due_at,
                        task_id,
                    ),
                )

            elif text is not None:
                await cur.execute(
                    """
                    UPDATE tasks
                    SET text = %s
                    WHERE id = %s
                    RETURNING id, text, due_at, status, created_at, completed_at
                    """,
                    (
                        text,
                        task_id,
                    ),
                )

            else:
                await cur.execute(
                    """
                    UPDATE tasks
                    SET due_at = %s
                    WHERE id = %s
                    RETURNING id, text, due_at, status, created_at, completed_at
                    """,
                    (
                        due_at,
                        task_id,
                    ),
                )

            row = await cur.fetchone()

            # A due-date change is the signal for a task that keeps slipping.
            if row is not None and due_at is not _UNSET and row[2] != old_due:
                await cur.execute(DUE_LOG_SQL, (task_id, old_due, row[2]))

    if row is None:
        return None

    return _row_to_dict(row)

async def complete_task(
    pool,
    *,
    task_id: int,
    completed_at: datetime | None = None,
) -> dict[str, Any] | None:
    if completed_at is None:
        completed_at = datetime.now().astimezone()

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                COMPLETE_SQL,
                (
                    completed_at,
                    task_id,
                ),
            )

            row = await cur.fetchone()

            if row is None:
                return None

            task = _row_to_dict(row)

            await cur.execute(
                TASK_LOG_SQL,
                (
                    task["id"],
                    "completed",
                    json.dumps(task, default=str),
                ),
            )

    return task


async def delete_task(
    pool,
    *,
    task_id: int,
) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                GET_SQL,
                (task_id,),
            )

            row = await cur.fetchone()

            if row is None:
                return False

            task = _row_to_dict(row)

            await cur.execute(
                DELETE_SQL,
                (task_id,),
            )

            deleted_row = await cur.fetchone()

            if deleted_row is None:
                return False

            await cur.execute(
                TASK_LOG_SQL,
                (
                    task["id"],
                    "deleted",
                    json.dumps(task, default=str),
                ),
            )

            return True
async def expire_tasks(
    pool,
    *,
    now: datetime | None = None,
    grace_period: timedelta = timedelta(days=3),
) -> int:
    if now is None:
        now = datetime.now().astimezone()

    cutoff = now - grace_period
    expired = 0

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                EXPIRE_SQL,
                (cutoff,),
            )

            rows = await cur.fetchall()

            for row in rows:
                task = _row_to_dict(row)

                await cur.execute(
                    DELETE_SQL,
                    (task["id"],),
                )

                deleted_row = await cur.fetchone()

                if deleted_row is None:
                    continue

                await cur.execute(
                    TASK_LOG_SQL,
                    (
                        task["id"],
                        "expired",
                        json.dumps(task, default=str),
                    ),
                )

                expired += 1

    return expired


async def list_task_log(
    pool,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Completed, deleted and expired tasks, newest event first."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(TASK_LOG_LIST_SQL, (limit,))
            rows = await cur.fetchall()

    return [
        {
            "id": row[0],
            "task_id": row[1],
            "action": row[2],
            "snapshot": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]
