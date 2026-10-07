from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Mapping
from storage.embedder import Embedder

log = logging.getLogger(__name__)
DEFAULT_MIN_SIMILARITY = 0.65


@dataclass(frozen=True)
class SearchResult:
    id: int | str
    score: float
    content: str
    metadata: dict[str, Any]


class PostgresVectorStore:
    """
    pgvector-backed vector storage.

    This class owns all Postgres vector operations.
    """

    SUPPORTED_TABLES = {
        "notes": "notes",
    }

    def __init__(self, pool) -> None:
        self.pool = pool

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        return "[" + ",".join(str(float(value)) for value in vector) + "]"

    def _table_name(self, table: str) -> str:
        if not isinstance(table, str):
            raise ValueError("table must be a string")

        table = table.strip()

        if table not in self.SUPPORTED_TABLES:
            raise ValueError(f"unsupported vector table: {table}")

        return self.SUPPORTED_TABLES[table]

    async def index(
        self,
        *,
        table: str,
        record_id: int | str,
        vector: list[float],
        model: str,
        content: str,
    ) -> bool:
        table_name = self._table_name(table)

        if table_name != "notes":
            raise ValueError(f"unsupported vector table: {table}")
        if len(vector) != 384:
                    raise ValueError(
                        f"expected 384-dimensional vector, got {len(vector)}"
                    )
        vector_literal = self._vector_literal(vector)

        sql = f"""
            UPDATE {table_name}
            SET
                embedding = %s::vector,
                embedding_model = %s,
                embedding_status = 'done'
            WHERE id = %s
            AND text = %s
            AND embedding_status IN ('pending', 'failed')
        """
        
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    (
                        vector_literal,
                        model,
                        record_id,
                        content,
                    ),
                )

                if cur.rowcount != 1:
                    return False

        return True

    async def search(
        self,
        *,
        table: str,
        vector: list[float],
        model: str,
        k: int = 5,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        table_name = self._table_name(table)

        if k <= 0:
            return []
        if not 0.0 <= min_similarity <= 1.0:
            raise ValueError("min_similarity must be between 0.0 and 1.0")
        if len(vector) != 384:
            raise ValueError(
                f"expected 384-dimensional vector, got {len(vector)}"
            )
        vector_literal = self._vector_literal(vector)
        filters = filters or {}

        where = [
            "n.embedding IS NOT NULL",
            "n.embedding_model = %s",
            "n.embedding_status = 'done'",
        ]

        params: list[Any] = [
            vector_literal,
            model,
        ]

        source = filters.get("source")

        if source is not None:
            where.append("n.source = %s")
            params.append(source)

        tag = filters.get("tag")

        if tag is not None:
            where.append("n.tags @> %s::jsonb")
            params.append(json.dumps([tag]))

        params.extend([min_similarity, k])

        sql = f"""
            WITH query_vector AS (
                SELECT %s::vector AS embedding
            )
            SELECT
                n.id,
                n.text,
                n.tags,
                n.source,
                n.created_at,
                n.embedding_model,
                1 - (n.embedding <=> q.embedding) AS score
            FROM notes n
            CROSS JOIN query_vector q
            WHERE {" AND ".join(where)}
            AND 1 - (n.embedding <=> q.embedding) >= %s
            ORDER BY n.embedding <=> q.embedding
            LIMIT %s
        """

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()

        results: list[SearchResult] = []

        for row in rows:
            (
                record_id,
                text,
                tags,
                source,
                created_at,
                embedding_model,
                score,
            ) = row

            results.append(
                SearchResult(
                    id=record_id,
                    score=float(score),
                    content=text,
                    metadata={
                        "tags": tags,
                        "source": source,
                        "created_at": created_at.isoformat()
                        if created_at is not None
                        else None,
                        "embedding_model": embedding_model,
                    },
                )
            )

        return results


class Retriever:
    """
    B.4 retrieval boundary.

    Owns:
      - embedding generation
      - vector indexing
      - vector search

    Callers never interact with Embedder directly.
    """

    def __init__(
        self,
        *,
        pool,
        embedder: Embedder | None = None,
        vector_store: PostgresVectorStore | None = None,
    ) -> None:
        self.pool = pool
        self.embedder = embedder or Embedder()
        self.vector_store = vector_store or PostgresVectorStore(pool)

    async def index(
        self,
        *,
        table: str,
        record_id: int | str,
        content: str,
    ) -> bool:
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        original_content = content
        content = content.strip()

        if not content:
            return False

        vector = await asyncio.to_thread(
            self.embedder.embed,
            content,
)
        return await self.vector_store.index(
            table=table,
            record_id=record_id,
            vector=vector,
            model=self.embedder.model_name,
            content=original_content,
        )

    async def search(
        self,
        *,
        table: str,
        query: str,
        k: int = 5,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if not isinstance(query, str):
            return []

        query = " ".join(query.strip().split())

        if not query or k <= 0:
            return []

        vector = await asyncio.to_thread(
            self.embedder.embed,
            query,
        )

        return await self.vector_store.search(
            table=table,
            vector=vector,
            model=self.embedder.model_name,
            k=k,
            filters=filters,
            min_similarity=min_similarity,
        )


async def index(
    pool,
    *,
    table: str,
    record_id: int | str,
    content: str,
    embedder: Embedder | None = None,
) -> bool:
    retriever = Retriever(
        pool=pool,
        embedder=embedder,
    )

    return await retriever.index(
        table=table,
        record_id=record_id,
        content=content,
    )


async def search(
    pool,
    *,
    table: str,
    query: str,
    k: int = 5,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    filters: dict[str, Any] | None = None,
    embedder: Embedder | None = None,
) -> list[SearchResult]:
    retriever = Retriever(
        pool=pool,
        embedder=embedder,
    )

    return await retriever.search(
        table=table,
        query=query,
        k=k,
        filters=filters,
        min_similarity=min_similarity,
    )