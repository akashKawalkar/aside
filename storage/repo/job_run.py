from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


async def insert_job_run(pool, *, kind: str, step: str, day: date | None, ok: bool, attempt: int = 1,
                         error: str | None = None, detail: dict[str, Any] | None = None) -> int:
    """One row per step attempt, written when the step finishes (a crash mid-step leaves no row)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO job_run (kind, step, day, ok, attempt, error, detail, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
                (kind, step, day, ok, attempt, error, Jsonb(detail or {})),
            )
            return (await cur.fetchone())[0]


async def step_done(pool, *, day: date, step: str) -> bool:
    """Did this step already succeed for this day (and did it do something, not just skip)? Makes reruns a no-op."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM job_run WHERE day = %s AND step = %s AND ok AND coalesce(detail->>'skipped', '') = '' LIMIT 1",
                (day, step),
            )
            return await cur.fetchone() is not None


async def list_job_runs(pool, *, limit: int = 100, day: date | None = None) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT id, started_at, finished_at, kind, step, day, ok, attempt, error, detail FROM job_run
                   WHERE (%s::date IS NULL OR day = %s::date) ORDER BY id DESC LIMIT %s""",
                (day, day, limit),
            )
            return await cur.fetchall()
