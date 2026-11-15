"""One JSON-returning model call with the retry rule: 3 attempts with backoff on busy/slow/server errors, then it raises.
There is no template fallback; the caller (the nightly job) reports the failure the way other failures are reported."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from llm.client import LLMClient, Message
from llm.errors import LLMRateLimited, LLMServerError, LLMTimeout, LLMUnavailable

ATTEMPTS = 3
BACKOFF = (2.0, 8.0)          # seconds before attempt 2 and attempt 3
RETRYABLE = (LLMRateLimited, LLMServerError, LLMTimeout, LLMUnavailable)


class BadReply(ValueError):
    """The model answered, but not with usable JSON. `text` is what it said, so the caller can show it back."""

    def __init__(self, message: str, text: str = "") -> None:
        super().__init__(message)
        self.text = text


def parse_json(text: str) -> Any:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t, strict=False)         # a raw newline inside a string is tolerated
    except json.JSONDecodeError as e:
        raise BadReply(f"reply is not JSON: {e}") from None


async def call_json(
    client: LLMClient, messages: list[Message], *, max_tokens: int, temperature: float = 0.0,
    sleep=asyncio.sleep, backoff: tuple[float, ...] = BACKOFF,
) -> tuple[Any, Any]:
    """(parsed reply, the Completion). Retries only provider trouble; a reply that is not JSON raises BadReply at once so
    the caller can decide (the planner re-asks with the violations listed)."""
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            await sleep(backoff[min(attempt - 1, len(backoff) - 1)])
        try:
            done = await client.complete(messages, response_format={"type": "json_object"}, max_tokens=max_tokens, temperature=temperature)
        except RETRYABLE as e:
            last = e
            continue
        try:
            return parse_json(done.text), done
        except BadReply as e:
            raise BadReply(str(e), done.text) from None
    raise last or LLMUnavailable("model call failed")
