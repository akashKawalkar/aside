"""Persistence for code-derived pattern observations."""
from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.types.json import Jsonb


COLUMNS = "id, observation_key, kind, text, status, occurrences, misses, first_seen, last_seen, last_evaluated_day, evidence, updated_at"


async def list_observations(pool, *, include_dropped: bool = True) -> list[dict[str, Any]]:
    where = "deleted_at IS NULL"
    if not include_dropped:
        where += " AND status <> 'dropped'"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT {COLUMNS} FROM observations WHERE {where} ORDER BY "
                "CASE status WHEN 'active' THEN 0 WHEN 'questioned' THEN 1 WHEN 'candidate' THEN 2 ELSE 3 END, "
                "occurrences DESC, last_seen DESC NULLS LAST, id"
            )
            rows = await cur.fetchall()
    keys = [column.strip() for column in COLUMNS.split(",")]
    return [dict(zip(keys, row)) for row in rows]


async def upsert_observation(pool, item: dict[str, Any]) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO observations
                       (observation_key, kind, text, status, occurrences, misses, first_seen, last_seen,
                        last_evaluated_day, evidence)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (observation_key) DO UPDATE SET
                       kind = EXCLUDED.kind,
                       text = EXCLUDED.text,
                       status = EXCLUDED.status,
                       occurrences = EXCLUDED.occurrences,
                       misses = EXCLUDED.misses,
                       first_seen = EXCLUDED.first_seen,
                       last_seen = EXCLUDED.last_seen,
                       last_evaluated_day = EXCLUDED.last_evaluated_day,
                       evidence = EXCLUDED.evidence,
                       updated_at = now()
                   WHERE observations.deleted_at IS NULL""",
                (
                    item["observation_key"], item["kind"], item["text"], item["status"],
                    item["occurrences"], item["misses"], item.get("first_seen"), item.get("last_seen"),
                    item.get("last_evaluated_day"), Jsonb(item.get("evidence", {})),
                ),
            )


async def mark_observation_deleted(pool, observation_id: int) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE observations
                   SET deleted_at = now(), text = '', evidence = '{}'::jsonb, occurrences = 0,
                       misses = 0, first_seen = NULL, last_seen = NULL
                   WHERE id = %s AND deleted_at IS NULL RETURNING id""",
                (observation_id,),
            )
            return await cur.fetchone() is not None
