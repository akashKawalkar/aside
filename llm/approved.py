# llm/approved.py — the one door to a real model. Wraps any LLMClient so a call can only go out inside an explicit
# grant, only while the day's quota lasts, and always leaves a trace (success or failure). Plan §3.9.
#
# Why a wrapper and not a check in the route: the free-tier allowance is the thing being protected, and a
# convention ("remember to call the quota check") is exactly what the next feature forgets. A caller that builds a
# client through llm.factory.create_client cannot skip the gate; a caller that has no grant gets ApprovalRequired.
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from llm.client import Completion, LLMClient, Message, ModelProfile
from llm.errors import LLMError
from llm.trace import SaveSpan, Span, record, span_from_completion

CountToday = Callable[[], Awaitable[int]]   # model calls made so far today (IST), failed ones included


class ApprovalRequired(LLMError):
    """A call was attempted with no grant: the user did not approve it and it is not an enabled background job."""


class QuotaExceeded(LLMError):
    """Today's call cap is used up. The call was NOT made and uses no allowance."""

    def __init__(self, used: int, cap: int) -> None:
        super().__init__(f"daily LLM call cap reached ({used}/{cap}); raise [llm] daily_call_cap in config.toml to allow more")
        self.used, self.cap = used, cap


@dataclass(frozen=True)
class Grant:
    """Permission for the calls made inside a `with approved(...)` block."""
    tier: Literal["user", "background"]      # user = they read and approved this exact call; background = unattended
    operation: str = "chat"                  # label for the trace ("chat", "extraction", "schedule", ...)
    compile_log_id: int | None = None        # the context compile the call used, for replay and `wrong:`


_grant: ContextVar[Grant | None] = ContextVar("llm_grant", default=None)


@contextmanager
def approved(grant: Grant) -> Iterator[Grant]:
    token = _grant.set(grant)
    try:
        yield grant
    finally:
        _grant.reset(token)


class ApprovedClient:
    """An LLMClient that refuses to run without a grant or past the daily cap, and traces every call it does run."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        count_today: CountToday,
        daily_call_cap: int,
        background_enabled: bool = False,
        save_span: SaveSpan | None = None,
    ) -> None:
        self._inner = inner
        self._count_today = count_today
        self._cap = daily_call_cap
        self._background_enabled = background_enabled
        self._save_span = save_span

    @property
    def profile(self) -> ModelProfile:
        return self._inner.profile

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        grant = _grant.get()
        if grant is None:
            raise ApprovalRequired("no approved grant for this model call")
        if grant.tier == "background" and not self._background_enabled:
            raise ApprovalRequired("background model calls are disabled ([llm] background_enabled in config.toml)")

        used = await self._count_today()
        if used >= self._cap:
            raise QuotaExceeded(used, self._cap)

        attrs = {"max_tokens": max_tokens, "temperature": temperature, "tier": grant.tier, "reasoning_effort": self.profile.reasoning_effort}
        started = time.perf_counter()
        try:
            completion = await self._inner.complete(
                messages, tools=tools, response_format=response_format, max_tokens=max_tokens, temperature=temperature
            )
        except Exception as exc:
            # A failed call still used allowance, so it is traced (and counted) like any other.
            await self._trace(Span(
                operation=grant.operation, provider=self.profile.provider, model=self.profile.name,
                latency_ms=(time.perf_counter() - started) * 1000, error=f"{type(exc).__name__}: {exc}",
                compile_log_id=grant.compile_log_id, attrs=attrs,
            ))
            raise

        await self._trace(span_from_completion(
            completion, self.profile, operation=grant.operation, compile_log_id=grant.compile_log_id, attrs=attrs,
        ))
        return completion

    async def _trace(self, span: Span) -> None:
        try:
            await record(span, save=self._save_span)
        except Exception:    # a broken trace table must not turn a delivered answer into an error
            import logging
            logging.getLogger(__name__).warning("could not persist LLM trace", exc_info=True)
