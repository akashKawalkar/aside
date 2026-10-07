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

SEARCH_SQL = """
    SELECT
        id,
        text,
        tags,
        source,
        created_at,
        CASE
            WHEN text ILIKE %s THEN 'text'
            WHEN tags::text ILIKE %s THEN 'tag'
            ELSE NULL
        END AS match
    FROM notes
    WHERE text ILIKE %s
       OR tags::text ILIKE %s
    ORDER BY created_at DESC
    LIMIT %s
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
) -> list[dict[str, Any]]:
    if not isinstance(query, str):
        raise TypeError("query must be a string")

    query = query.strip()

    if not query:
        return []

    if limit <= 0:
        return []

    query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{query}%"

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                SEARCH_SQL,
                (
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    limit,
                ),
            )

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