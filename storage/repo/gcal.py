# storage/repo/gcal.py — the database side of Google Calendar / Tasks sync (see gcal/sync.py). Plain functions over the pool.
from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from storage.repo.schedule import _log, _row_to_dict

SYNC_COLUMNS = ("id, title, start_at, end_at, created_at, updated_at, origin, edited_by_user, "
                "gcal_event_id, gcal_updated, deleted_at, task_id")
TASK_COLUMNS = "id, text, due_at, status, created_at, completed_at, gtask_id, gtask_updated, slip_count"


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    """The columns schedule_log and the API know, from a row that also carries the sync columns."""
    return _row_to_dict((row["id"], row["title"], row["start_at"], row["end_at"], row["created_at"], row["updated_at"],
                         row["origin"], row["edited_by_user"]))


async def list_sync_entries(pool, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Every schedule row overlapping the window INCLUDING soft-deleted ones (sync needs to see those)."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {SYNC_COLUMNS} FROM schedule WHERE start_at < %s AND end_at > %s ORDER BY start_at, id", (end, start)
            )
            return await cur.fetchall()


async def get_entry_by_gcal_id(pool, gcal_event_id: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {SYNC_COLUMNS} FROM schedule WHERE gcal_event_id = %s", (gcal_event_id,))
            return await cur.fetchone()


async def get_entry(pool, schedule_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {SYNC_COLUMNS} FROM schedule WHERE id = %s", (schedule_id,))
            return await cur.fetchone()


async def insert_synced_entry(pool, *, title: str, start_at: datetime, end_at: datetime, gcal_event_id: str,
                              gcal_updated: datetime, task_id: int | None = None, origin: str = "user") -> int:
    """An event made on the calendar side becomes a schedule row (origin `user`: Google-side changes count as the user's)."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""INSERT INTO schedule (title, start_at, end_at, origin, gcal_event_id, gcal_updated, task_id, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {SYNC_COLUMNS}""",
                (title, start_at, end_at, origin, gcal_event_id, gcal_updated, task_id, gcal_updated),
            )
            row = await cur.fetchone()
            await _log(cur, row["id"], "create", None, _entry(row))
            return row["id"]


async def apply_calendar_change(pool, schedule_id: int, *, title: str, start_at: datetime, end_at: datetime,
                                gcal_updated: datetime) -> None:
    """The calendar's version won: copy it onto the row. A generated entry changed this way counts as edited by the user."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {SYNC_COLUMNS} FROM schedule WHERE id = %s FOR UPDATE", (schedule_id,))
            before = await cur.fetchone()
            if before is None:
                return
            await cur.execute(
                f"""UPDATE schedule SET title = %s, start_at = %s, end_at = %s, gcal_updated = %s, updated_at = %s,
                           edited_by_user = edited_by_user OR origin = 'generated'
                    WHERE id = %s RETURNING {SYNC_COLUMNS}""",
                (title, start_at, end_at, gcal_updated, gcal_updated, schedule_id),
            )
            after = await cur.fetchone()
            await _log(cur, schedule_id, "edit", _entry(before), _entry(after))


async def touch_synced(pool, schedule_id: int, *, gcal_event_id: str, gcal_updated: datetime) -> None:
    """Record that the row and its event agree as of `gcal_updated` (after a push, or a pull that found no difference)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE schedule SET gcal_event_id = %s, gcal_updated = %s, updated_at = %s WHERE id = %s",
                              (gcal_event_id, gcal_updated, gcal_updated, schedule_id))


async def soft_delete_from_calendar(pool, schedule_id: int, *, at: datetime) -> None:
    """The event was deleted on the calendar: the plan changed. The row is kept (soft-deleted) as data, and the linked task
    is NOT completed by this. `gcal_updated` is set to the deletion time so the next push does not try to delete it again."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {SYNC_COLUMNS} FROM schedule WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (schedule_id,))
            before = await cur.fetchone()
            if before is None:
                return
            await cur.execute("UPDATE schedule SET deleted_at = %s, gcal_updated = %s WHERE id = %s", (at, at, schedule_id))
            await _log(cur, schedule_id, "delete", _entry(before), None)


# ---------- tasks (the database holds the truth; Google Tasks mirrors it) ----------

async def list_task_sync_rows(pool) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {TASK_COLUMNS} FROM tasks ORDER BY id")
            return await cur.fetchall()


async def set_task_gtask(pool, task_id: int, *, gtask_id: str | None, gtask_updated: datetime | None) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE tasks SET gtask_id = %s, gtask_updated = %s WHERE id = %s", (gtask_id, gtask_updated, task_id))


async def insert_task_from_google(pool, *, text: str, due_at: datetime | None, gtask_id: str, gtask_updated: datetime) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO tasks (text, due_at, gtask_id, gtask_updated) VALUES (%s, %s, %s, %s) RETURNING id",
                (text, due_at, gtask_id, gtask_updated),
            )
            return (await cur.fetchone())[0]


async def drop_task(pool, task_id: int, *, reason: str, at: datetime) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE tasks SET status = 'dropped', dropped_at = %s, dropped_reason = %s WHERE id = %s AND status = 'pending'",
                (at, reason, task_id),
            )
