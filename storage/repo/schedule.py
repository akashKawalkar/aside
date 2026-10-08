from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

COLUMNS = "id, title, start_at, end_at, created_at, updated_at, origin, edited_by_user"


CREATE_SCHEDULE_SQL = f"""
    INSERT INTO schedule (title, start_at, end_at, origin, task_id)
    VALUES (%s, %s, %s, %s, %s)
    RETURNING {COLUMNS}
"""


LIST_SCHEDULE_RANGE_SQL = f"""
    SELECT {COLUMNS}
    FROM schedule
    WHERE start_at < %s
      AND end_at > %s
      AND deleted_at IS NULL
    ORDER BY start_at ASC, id ASC
"""


CURRENT_AND_NEXT_SQL = f"""
    SELECT {COLUMNS}
    FROM schedule
    WHERE start_at <= %s
      AND end_at > %s
      AND deleted_at IS NULL
    ORDER BY start_at ASC, id ASC
"""


NEXT_SCHEDULE_SQL = f"""
    SELECT {COLUMNS}
    FROM schedule
    WHERE start_at > %s
      AND deleted_at IS NULL
    ORDER BY start_at ASC, id ASC
    LIMIT 1
"""


GET_FOR_UPDATE_SQL = f"""
    SELECT {COLUMNS}
    FROM schedule
    WHERE id = %s
      AND deleted_at IS NULL
    FOR UPDATE
"""


# Changing a generated entry marks it as touched by the user.
UPDATE_SCHEDULE_SQL = f"""
    UPDATE schedule
    SET
        title = %s,
        start_at = %s,
        end_at = %s,
        edited_by_user = edited_by_user OR origin = 'generated',
        updated_at = now()
    WHERE id = %s
    RETURNING {COLUMNS}
"""


DELETE_SCHEDULE_SQL = """
    DELETE FROM schedule
    WHERE id = %s
    RETURNING id
"""


LOG_SQL = """
    INSERT INTO schedule_log (schedule_id, action, before, after)
    VALUES (%s, %s, %s, %s)
"""


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "title": row[1],
        "start_at": row[2],
        "end_at": row[3],
        "created_at": row[4],
        "updated_at": row[5],
        "origin": row[6],
        "edited_by_user": row[7],
    }


def _loggable(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """The fields worth keeping in the edit log, with times as ISO strings."""
    if entry is None:
        return None
    return {
        "title": entry["title"],
        "start_at": entry["start_at"].isoformat(),
        "end_at": entry["end_at"].isoformat(),
        "origin": entry["origin"],
        "edited_by_user": entry["edited_by_user"],
    }


async def _log(cur, schedule_id: int, action: str, before, after) -> None:
    await cur.execute(
        LOG_SQL,
        (schedule_id, action, Jsonb(_loggable(before)) if before else None, Jsonb(_loggable(after)) if after else None),
    )


async def create_schedule_entry(
    pool,
    *,
    title: str,
    start_at: datetime,
    end_at: datetime,
    origin: str = "user",
    task_id: int | None = None,
) -> dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(CREATE_SCHEDULE_SQL, (title, start_at, end_at, origin, task_id))
            entry = _row_to_dict(await cur.fetchone())
            await _log(cur, entry["id"], "create", None, entry)

    return entry


async def list_schedule_range(
    pool,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                LIST_SCHEDULE_RANGE_SQL,
                (end, start),
            )
            rows = await cur.fetchall()

    return [_row_to_dict(row) for row in rows]


async def get_current_and_next(
    pool,
    now: datetime,
) -> dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                CURRENT_AND_NEXT_SQL,
                (now, now),
            )
            current_rows = await cur.fetchall()

            await cur.execute(
                NEXT_SCHEDULE_SQL,
                (now,),
            )
            next_row = await cur.fetchone()

    return {
        "current": [_row_to_dict(row) for row in current_rows],
        "next": _row_to_dict(next_row) if next_row else None,
    }


async def update_schedule_entry(
    pool,
    schedule_id: int,
    *,
    title: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(GET_FOR_UPDATE_SQL, (schedule_id,))
            before_row = await cur.fetchone()
            if before_row is None:
                return None

            await cur.execute(UPDATE_SCHEDULE_SQL, (title, start_at, end_at, schedule_id))
            after = _row_to_dict(await cur.fetchone())
            await _log(cur, schedule_id, "edit", _row_to_dict(before_row), after)

    return after


async def delete_schedule_entry(
    pool,
    schedule_id: int,
) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(GET_FOR_UPDATE_SQL, (schedule_id,))
            before_row = await cur.fetchone()
            if before_row is None:
                return False

            await cur.execute("SELECT gcal_event_id FROM schedule WHERE id = %s", (schedule_id,))
            if (await cur.fetchone())[0]:
                # Mirrored on the calendar: keep the row (soft delete) until the next push removes the event.
                await cur.execute("UPDATE schedule SET deleted_at = now() WHERE id = %s", (schedule_id,))
            else:
                await cur.execute(DELETE_SCHEDULE_SQL, (schedule_id,))
            await _log(cur, schedule_id, "delete", _row_to_dict(before_row), None)

    return True
