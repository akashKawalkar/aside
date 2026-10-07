from __future__ import annotations

from datetime import datetime
from typing import Any


FETCH_EVENTS_SQL = """
    SELECT id, source, kind, app, window_title, domain, ts_start, ts_end, payload
    FROM events
    WHERE ts_start >= %s
      AND ts_start < %s
      AND ts_end <= %s
      AND kind IN ('app_focus', 'idle')
    ORDER BY ts_start, id
"""

# first_event_id is unique, so a session that already exists is skipped.
INSERT_SESSION_SQL = """
    INSERT INTO sessions
        (kind, app, started_at, ended_at, active_seconds, event_count, first_event_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (first_event_id) DO NOTHING
    RETURNING id
"""

LINK_EVENTS_SQL = """
    UPDATE events
    SET session_id = %s
    WHERE id = ANY(%s)
      AND session_id IS NULL
"""

LIST_SESSIONS_RANGE_SQL = """
    SELECT id, kind, app, started_at, ended_at, active_seconds, event_count
    FROM sessions
    WHERE started_at < %s
      AND ended_at > %s
    ORDER BY started_at ASC, id ASC
"""

_EVENT_COLUMNS = (
    "id", "source", "kind", "app", "window_title",
    "domain", "ts_start", "ts_end", "payload",
)


async def fetch_events_range(
    pool,
    *,
    start: datetime,
    end: datetime,
    settled_before: datetime,
) -> list[dict[str, Any]]:
    """
    Events that start in [start, end) and have finished by settled_before,
    oldest first. Events still in progress, or too recent to be sure that
    nothing earlier is still waiting to be ingested, are left out.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(FETCH_EVENTS_SQL, (start, end, settled_before))
            rows = await cur.fetchall()

    return [dict(zip(_EVENT_COLUMNS, row)) for row in rows]


async def save_sessions(
    pool,
    sessions: list[dict[str, Any]],
) -> int:
    """
    Store sessions and link their events, all in one transaction.

    Each session is {kind, app, started_at, ended_at, active_seconds,
    event_ids}; the first event id identifies the session, so saving the
    same session twice is a no-op. Returns how many were newly created.
    """
    created = 0

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for session in sessions:
                event_ids = list(session["event_ids"])

                if not event_ids:
                    raise ValueError("a session needs at least one event")

                await cur.execute(
                    INSERT_SESSION_SQL,
                    (
                        session["kind"],
                        session["app"],
                        session["started_at"],
                        session["ended_at"],
                        session["active_seconds"],
                        len(event_ids),
                        event_ids[0],
                    ),
                )
                row = await cur.fetchone()

                if row is None:
                    continue

                await cur.execute(LINK_EVENTS_SQL, (row[0], event_ids))
                created += 1

    return created


async def list_sessions_range(
    pool,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Sessions overlapping [start, end), oldest first."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(LIST_SESSIONS_RANGE_SQL, (end, start))
            rows = await cur.fetchall()

    return [
        {
            "id": row[0],
            "kind": row[1],
            "app": row[2],
            "started_at": row[3],
            "ended_at": row[4],
            "active_seconds": row[5],
            "event_count": row[6],
        }
        for row in rows
    ]
