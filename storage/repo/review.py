from __future__ import annotations

from datetime import datetime
from typing import Any


COMPLETED_TASKS_SQL = """
    SELECT id, text, completed_at
    FROM tasks
    WHERE status = 'completed'
      AND completed_at >= %s
      AND completed_at < %s
    ORDER BY completed_at ASC, id ASC
"""

COUNT_NOTES_SQL = """
    SELECT count(*)
    FROM notes
    WHERE created_at >= %s
      AND created_at < %s
"""

# Not yet overdue: due_at must fall inside the window itself.
DUE_TASKS_SQL = """
    SELECT id, text, due_at
    FROM tasks
    WHERE status = 'pending'
      AND due_at >= %s
      AND due_at < %s
    ORDER BY due_at ASC, id ASC
"""


async def list_completed_tasks(
    pool,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Tasks completed in [start, end), oldest first."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(COMPLETED_TASKS_SQL, (start, end))
            rows = await cur.fetchall()

    return [
        {"id": row[0], "text": row[1], "completed_at": row[2]}
        for row in rows
    ]


async def count_notes(
    pool,
    *,
    start: datetime,
    end: datetime,
) -> int:
    """Notes captured in [start, end)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(COUNT_NOTES_SQL, (start, end))
            row = await cur.fetchone()

    return int(row[0])


async def list_tasks_due(
    pool,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Pending tasks that fall due in [start, end), soonest first."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(DUE_TASKS_SQL, (start, end))
            rows = await cur.fetchall()

    return [
        {"id": row[0], "text": row[1], "due_at": row[2]}
        for row in rows
    ]
