from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


INSERT_SQL = """
    INSERT INTO notes (
        text,
        tags,
        source,
        created_at,
        embedding_status
    )
    VALUES (%s, %s, %s, %s, 'pending')
    RETURNING id, text, tags, source, created_at, embedding_status
"""


PENDING_EMBEDDINGS_SQL = """
    SELECT
        id,
        text
    FROM notes
    WHERE embedding_status = 'pending' OR (embedding_status = 'failed' AND embedding_retries < 3)
    ORDER BY created_at ASC
    LIMIT %s
"""


MARK_EMBEDDING_FAILED_SQL = """
    UPDATE notes
    SET embedding_status = 'failed',
        embedding_retries = embedding_retries + 1
    WHERE id = %s
"""
UPDATE_NOTE_SQL = """
    UPDATE notes
    SET
        text = %s,
        tags = %s,
        embedding = NULL,
        embedding_model = NULL,
        embedding_status = 'pending'
    WHERE id = %s
    RETURNING id, text, tags, source, created_at, embedding_status
"""


DELETE_NOTE_SQL = """
    DELETE FROM notes
    WHERE id = %s
    RETURNING id
"""

LIST_NOTES_SQL = """
    SELECT
        id,
        text,
        tags,
        source,
        created_at,
        embedding_status
    FROM notes
    ORDER BY created_at DESC
    LIMIT %s
"""

async def save_note(
    pool,
    payload: dict[str, Any],
) -> dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                INSERT_SQL,
                (
                    payload["text"],
                    Jsonb(payload.get("tags", [])),
                    payload.get("source", "panel"),
                    payload["created_at"],
                ),
            )

            row = await cur.fetchone()

            return {
                "id": row[0],
                "text": row[1],
                "tags": row[2],
                "source": row[3],
                "created_at": row[4],
                "embedding_status": row[5],
            }


async def search_notes(
    pool,
    query: str,
    limit: int = 10,
    *,
    match_any: bool = False,
) -> list[dict[str, Any]]:
    """Keyword search over text and tags. Default: every word must appear (the search box narrows as you type).
    `match_any=True` is for natural-language questions used as context: any word of 3+ letters may match, and notes
    matching more words come first."""
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    query = query.strip()

    if not query:
        return []

    if limit <= 0:
        return []

    raw = [w for w in query.split() if len(w) >= 3] if match_any else query.split()
    words = [w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for w in raw]
    if not words:
        return []

    patterns = [f"%{w}%" for w in words]
    hit = "(text ILIKE %s OR tags::text ILIKE %s)"
    where_sql = f" {'OR' if match_any else 'AND'} ".join([hit] * len(patterns))
    score_sql = " + ".join([f"(CASE WHEN {hit} THEN 1 ELSE 0 END)"] * len(patterns))
    match_sql = "CASE WHEN " + " OR ".join(["text ILIKE %s"] * len(patterns)) + " THEN 'text' ELSE 'tag' END"

    search_sql = f"""
        SELECT id, text, tags, source, created_at, {match_sql} AS match
        FROM notes
        WHERE {where_sql}
        ORDER BY {score_sql} DESC, created_at DESC
        LIMIT %s
    """

    pair = [p for p in patterns for _ in (0, 1)]
    params = [*patterns, *pair, *pair, limit]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(search_sql, tuple(params))

            rows = await cur.fetchall()

            return [
                {
                    "id": row[0],
                    "text": row[1],
                    "tags": row[2],
                    "source": row[3],
                    "created_at": row[4],
                    "match": row[5],
                }
                for row in rows
            ]
async def get_pending_embeddings(
    pool,
    limit: int = 16,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                PENDING_EMBEDDINGS_SQL,
                (limit,),
            )

            rows = await cur.fetchall()

            return [
                {
                    "id": row[0],
                    "text": row[1],
                }
                for row in rows
            ]


async def mark_embedding_failed(
    pool,
    note_id: int,
) -> None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                MARK_EMBEDDING_FAILED_SQL,
                (note_id,),
            )
async def update_note(
    pool,
    note_id: int,
    *,
    text: str,
    tags: list[str],
) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                UPDATE_NOTE_SQL,
                (
                    text,
                    Jsonb(tags),
                    note_id,
                ),
            )

            row = await cur.fetchone()

            if row is None:
                return None

            return {
                "id": row[0],
                "text": row[1],
                "tags": row[2],
                "source": row[3],
                "created_at": row[4],
                "embedding_status": row[5],
            }


async def delete_note(
    pool,
    note_id: int,
) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                DELETE_NOTE_SQL,
                (note_id,),
            )

            return await cur.fetchone() is not None

async def list_notes(
    pool,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                LIST_NOTES_SQL,
                (limit,),
            )

            rows = await cur.fetchall()

            return [
                {
                    "id": row[0],
                    "text": row[1],
                    "tags": row[2],
                    "source": row[3],
                    "created_at": row[4],
                    "embedding_status": row[5],
                }
                for row in rows
            ]