# extractor/job.py
from __future__ import annotations

import json
from datetime import date
from typing import Any

from config import Privacy, load_config
from context.privacy import denied_reason
from llm.client import Message, LLMClient
from psycopg_pool import AsyncConnectionPool
from storage.repo.statements import get_statements_for_day, insert_candidate_item

ITEM_TYPES = {"actual", "plan", "prediction", "constraint", "preference"}

SYSTEM_PROMPT = """You extract candidate items from a day's journal entries and notes.
Output ONE JSON object containing a list of items:
{"items": [
  {
    "type": "actual",
    "text": "what the item is",
    "effect": "what it means",
    "valid_from": "YYYY-MM-DD",
    "valid_until": "YYYY-MM-DD",
    "confidence": 1.0,
    "source_id": "optional id of the statement",
    "reason": "why you extracted this"
  }
]}
Rules:
- Be conservative. If unsure, don't extract.
- type must be one of: "actual", "plan", "prediction", "constraint", "preference"
- "actual" is a fact that happened.
- "plan" is something the user intends to do.
- "prediction" is a guess about the future.
- "constraint" is a hard rule.
- "preference" is a soft rule.
"""

def _date_or_none(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def extract_for_day(
    client: LLMClient, pool: AsyncConnectionPool, target_date: date, *, privacy: Privacy | None = None,
) -> list[dict[str, Any]]:
    """ONE batched call over the day's statements (plan §3.5). Statements the privacy layer denies (by default the
    journal) are never sent; the source name is matched against both deny lists."""
    rules = privacy or load_config().privacy
    statements = [s for s in await get_statements_for_day(pool, target_date)
                  if denied_reason(s["source"], (s["source"],), rules) is None]
    if not statements:
        return []

    text_to_process = "\n".join([f"[{s['id']}] ({s['source']}): {s['text']}" for s in statements])
    messages = [
        Message("system", SYSTEM_PROMPT),
        Message("user", text_to_process)
    ]

    completion = await client.complete(
        messages,
        response_format={"type": "json_object"},
        max_tokens=4000
    )

    try:
        items = json.loads(completion.text).get("items", [])
    except (json.JSONDecodeError, AttributeError):
        items = []

    results = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("type") not in ITEM_TYPES or not str(item.get("text") or "").strip():
            continue        # a malformed item is skipped, never allowed to abort the rest of the batch
        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence", 1.0))))
        except (TypeError, ValueError):
            confidence = 1.0
        source_id = item.get("source_id")
        row = await insert_candidate_item(
            pool,
            item_type=item["type"],
            text=str(item["text"]).strip(),
            effect=item.get("effect"),
            valid_from=_date_or_none(item.get("valid_from")),
            valid_until=_date_or_none(item.get("valid_until")),
            confidence=confidence,
            source_id=str(source_id) if source_id not in (None, "", "null") else None,
            reason=item.get("reason"),
        )
        results.append(row)

    return results
