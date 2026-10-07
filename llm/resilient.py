# llm/resilient.py — when the provider is overloaded or silent, try one other model instead of failing the user's call.
#
# Why (measured 2026-10-07, docs/gemini_api_findings.md): latency is driven by server load, not by the request. The same
# schedule call took 2.6 s, 26 s and 92 s; one model answered HTTP 503 "high demand" three times in a row, instantly;
# several calls stalled for 60-150 s. Retrying the SAME model is the wrong answer to a 503 that lasts minutes, so the
# second attempt goes to a different model. Every attempt is a real, gated, traced call (each client passed in is an
# ApprovedClient), so the daily cap and llm_trace stay honest.
from __future__ import annotations

import dataclasses
from typing import Any

from llm.approved import ApprovalRequired, QuotaExceeded
from llm.client import Completion, LLMClient, Message, ModelProfile
from llm.errors import LLMError, LLMRateLimited, LLMServerError, LLMTimeout, LLMUnavailable

# Worth trying another model for. NOT: a rejected key or a malformed request (the fallback would fail the same way), and
# never the gate's own refusals (no approval / cap reached): those must stop the call.
RETRYABLE = (LLMServerError, LLMTimeout, LLMUnavailable, LLMRateLimited)


class FallbackClient:
    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self._primary, self._fallback = primary, fallback

    @property
    def profile(self) -> ModelProfile:
        return self._primary.profile

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        kwargs = dict(tools=tools, response_format=response_format, max_tokens=max_tokens, temperature=temperature)
        try:
            return await self._primary.complete(messages, **kwargs)
        except RETRYABLE as first:
            try:
                completion = await self._fallback.complete(messages, **kwargs)
            except (QuotaExceeded, ApprovalRequired):
                raise first from None                      # no allowance left for a second attempt: the original failure stands
            except LLMError as second:
                raise type(first)(f"{first} (the fallback {self._fallback.profile.name} also failed: {second})") from None
            return dataclasses.replace(completion, fallback_from=self._primary.profile.name)
