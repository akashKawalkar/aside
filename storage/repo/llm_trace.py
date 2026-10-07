# storage/repo/llm_trace.py — one row per model call (see llm/trace.py).
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

INSERT_SQL = """
    INSERT INTO llm_trace (ts, trace_id, span_id, parent_span_id, operation, provider, model,
                           input_tokens, output_tokens, latency_ms, finish_reason, error, compile_log_id, attrs)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""
LIST_SQL = """
    SELECT id, ts, trace_id, span_id, parent_span_id, operation, provider, model,
           input_tokens, output_tokens, latency_ms, finish_reason, error, compile_log_id, attrs
    FROM llm_trace
    ORDER BY id DESC
    LIMIT %s
"""


async def insert_llm_trace(pool, span: dict[str, Any]) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                INSERT_SQL,
                (span["ts"], span["trace_id"], span["span_id"], span["parent_span_id"], span["operation"],
                 span["provider"], span["model"], span["input_tokens"], span["output_tokens"], span["latency_ms"],
                 span["finish_reason"], span["error"], span["compile_log_id"], Jsonb(span["attrs"])),
            )
            return (await cur.fetchone())[0]


async def count_llm_traces_since(pool, since) -> int:
    """Model calls made at or after `since`, failed ones included (a failure still uses the provider's allowance)."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM llm_trace WHERE ts >= %s", (since,))
            return (await cur.fetchone())[0]


async def latest_sent_compile_log_id(pool) -> int | None:
    """The compile behind the last call that was actually dispatched. A compile is logged when it is staged, so the
    newest compile_log row may belong to a call the user rejected; a trace row exists only for a call that went out."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT compile_log_id FROM llm_trace WHERE compile_log_id IS NOT NULL ORDER BY id DESC LIMIT 1")
            row = await cur.fetchone()
            return row[0] if row else None


async def list_llm_traces(pool, *, limit: int = 50) -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(LIST_SQL, (limit,))
            rows = await cur.fetchall()
    keys = ("id", "ts", "trace_id", "span_id", "parent_span_id", "operation", "provider", "model", "input_tokens",
            "output_tokens", "latency_ms", "finish_reason", "error", "compile_log_id", "attrs")
    return [dict(zip(keys, row)) for row in rows]
