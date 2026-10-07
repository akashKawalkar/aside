# storage/repo/compile_log.py — one row per context compile: what was offered, chosen and dropped.
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

INSERT_SQL = """
    INSERT INTO compile_log (ts, situation, recipe_name, recipe_hash, model, offered, chosen, dropped, tokens, query)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""
COLUMNS = "id, ts, situation, recipe_name, recipe_hash, model, offered, chosen, dropped, tokens, query"
GET_SQL = f"SELECT {COLUMNS} FROM compile_log WHERE id = %s"
LIST_SQL = """
    SELECT id, ts, situation, recipe_name, recipe_hash, model, tokens, query,
           jsonb_array_length(chosen), jsonb_array_length(dropped)
    FROM compile_log
    ORDER BY id DESC
    LIMIT %s
"""


def _row_to_dict(row) -> dict[str, Any]:
    keys = ("id", "ts", "situation", "recipe_name", "recipe_hash", "model", "offered", "chosen", "dropped", "tokens", "query")
    return dict(zip(keys, row))


async def insert_compile_log(pool, log: dict[str, Any]) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                INSERT_SQL,
                (log["ts"], log["situation"], log["recipe_name"], log["recipe_hash"], log["model"],
                 Jsonb(log["offered"]), Jsonb(log["chosen"]), Jsonb(log["dropped"]), log["tokens"], log["query"]),
            )
            return (await cur.fetchone())[0]


async def get_compile_log(pool, log_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(GET_SQL, (log_id,))
            row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def list_compile_logs(pool, *, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, without the bulky offered/chosen/dropped blobs (only their counts)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(LIST_SQL, (limit,))
            rows = await cur.fetchall()
    keys = ("id", "ts", "situation", "recipe_name", "recipe_hash", "model", "tokens", "query", "chosen_count", "dropped_count")
    return [dict(zip(keys, row)) for row in rows]
