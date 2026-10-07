# storage/repo/data_quality.py — the small logs and nightly records that explain gaps and feed pattern-finding.
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb


# ---------- monitoring pause/resume ----------

async def insert_monitoring_log(pool, *, enabled: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO monitoring_log (enabled) VALUES (%s)", (enabled,))


async def list_monitoring_log(pool, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Switches inside [start, end), preceded by the last one before `start` so a pause that began earlier is known."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                (SELECT enabled, created_at FROM monitoring_log WHERE created_at < %s ORDER BY created_at DESC LIMIT 1)
                UNION ALL
                (SELECT enabled, created_at FROM monitoring_log WHERE created_at >= %s AND created_at < %s)
                ORDER BY created_at
                """,
                (start, start, end),
            )
            rows = await cur.fetchall()
    return [{"enabled": r[0], "at": r[1]} for r in rows]


# ---------- heartbeat samples ----------

async def insert_heartbeat_log(pool, *, collector_ok: bool, ingestor_ok: bool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO heartbeat_log (collector_ok, ingestor_ok) VALUES (%s, %s)", (collector_ok, ingestor_ok)
        )


async def list_heartbeats(pool, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ts, collector_ok, ingestor_ok FROM heartbeat_log WHERE ts >= %s AND ts < %s ORDER BY ts",
                (start, end),
            )
            rows = await cur.fetchall()
    return [{"ts": r[0], "collector_ok": r[1], "ingestor_ok": r[2]} for r in rows]


# ---------- nightly schedule snapshot and day record ----------

async def save_schedule_snapshot(pool, *, day: date, entries: list[dict[str, Any]], late: bool) -> bool:
    """False if that day already has one (the first snapshot of a day is the one that counts)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO schedule_snapshot (day, entries, late) VALUES (%s, %s, %s) ON CONFLICT (day) DO NOTHING RETURNING id",
                (day, Jsonb(entries), late),
            )
            return await cur.fetchone() is not None


async def save_day_record(pool, *, day: date, data: dict[str, Any], late: bool) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO day_record (day, data, late) VALUES (%s, %s, %s) ON CONFLICT (day) DO NOTHING RETURNING id",
                (day, Jsonb(data), late),
            )
            return await cur.fetchone() is not None


async def get_schedule_snapshot(pool, *, day: date) -> list[dict[str, Any]] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT entries FROM schedule_snapshot WHERE day = %s", (day,))
            row = await cur.fetchone()
    return row[0] if row else None


async def days_with_snapshot(pool, *, first: date, last: date) -> set[date]:
    return await _days(pool, "schedule_snapshot", first, last)


async def days_with_day_record(pool, *, first: date, last: date) -> set[date]:
    return await _days(pool, "day_record", first, last)


async def _days(pool, table: str, first: date, last: date) -> set[date]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT day FROM {table} WHERE day BETWEEN %s AND %s", (first, last))
            return {r[0] for r in await cur.fetchall()}


# ---------- coverage facts for the data-gaps view ----------

async def session_bounds(pool) -> tuple[datetime | None, datetime | None]:
    """Start of the first session and end of the last one, or (None, None) with no sessions."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT min(started_at), max(ended_at) FROM sessions")
            row = await cur.fetchone()
    return row[0], row[1]


async def last_day_record(pool) -> date | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT max(day) FROM day_record")
            return (await cur.fetchone())[0]


# ---------- mark-wrong ----------

async def insert_mark_wrong(pool, *, target: str, compile_log_id: int | None = None, reason: str | None = None) -> int:
    """Only logs it. Each mark keeps its compile id so it can become a replayable regression case."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO mark_wrong_log (target, compile_log_id, reason) VALUES (%s, %s, %s) RETURNING id",
                (target, compile_log_id, reason),
            )
            return (await cur.fetchone())[0]
