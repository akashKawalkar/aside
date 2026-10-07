# storage/repo/diff_log.py
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

INSERT_SQL = """
    INSERT INTO diff_log (source_type, source_id, before, after, removed, added)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id, created_at
"""

SELECT_ALL_SQL = """
    SELECT id, source_type, source_id, before, after, removed, added, created_at
    FROM diff_log
    ORDER BY id
"""


async def log_diff(
    pool,
    *,
    source_type: str,
    source_id: str | None,
    before: dict[str, Any],
    after: dict[str, Any],
    removed: dict[str, Any],
    added: dict[str, Any],
) -> dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                INSERT_SQL,
                (source_type, source_id, Jsonb(before), Jsonb(after), Jsonb(removed), Jsonb(added)),
            )
            row = await cur.fetchone()
            return {"id": row[0], "created_at": row[1]}


async def read_all_diffs(pool) -> list[dict[str, Any]]:
    """Every logged change, oldest first. `undone` is true when a later undo row points at it."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SELECT_ALL_SQL)
            rows = await cur.fetchall()
    entries = [
        {
            "id": r[0], "source_type": r[1], "source_id": r[2],
            "before": r[3], "after": r[4], "removed": r[5], "added": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]
    undone = {e["source_id"] for e in entries if e["source_type"] == "undo"}
    for e in entries:
        e["undone"] = str(e["id"]) in undone
    return entries
