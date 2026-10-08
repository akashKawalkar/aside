from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


async def get_review_state(pool, key: str) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT value FROM review_state WHERE key = %s", (key,))
            row = await cur.fetchone()
    return row[0] if row else None


async def set_review_state(pool, key: str, value: dict[str, Any]) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO review_state (key, value) VALUES (%s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                (key, Jsonb(value)),
            )
