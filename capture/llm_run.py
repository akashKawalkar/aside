# capture/llm_run.py — a model call the user just asked for (chat, "draft tomorrow"). It goes straight through the gated
# client (llm/approved.py): a user-tier grant, the daily cap, a trace on success AND failure, one fallback model when the
# chosen one is overloaded. There is no approval step any more; the cap and the privacy layer are the guards.
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import load_config
from llm.approved import Grant, approved
from llm.client import Completion, Message, ModelProfile, load_profile
from llm.factory import create_client
from llm.quota import provider_day_start
from storage import count_llm_traces_since, insert_llm_trace

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


async def calls_today(pool) -> int:
    """Model calls made in the provider's current day (it resets at midnight Pacific), failed ones included. Counted from
    llm_trace itself, so it survives a restart and needs no table of its own."""
    return await count_llm_traces_since(pool, provider_day_start(datetime.now(IST)))


async def run_user_call(
    app: Any, *, profile: ModelProfile, messages: list[Message], operation: str, compile_log_id: int | None,
    max_tokens: int = 2000, temperature: float = 0.7, response_format: dict[str, Any] | None = None,
) -> Completion:
    """Raises llm.approved.QuotaExceeded when today's cap is used up (nothing is sent), or the provider's error."""
    pool = app.state.db_pool
    cfg = load_config().llm

    async def save_span(row):
        try:
            await insert_llm_trace(pool, row)
        except Exception:
            logger.warning("Could not persist LLM trace to database")

    fallback = None
    if cfg.fallback_model and cfg.fallback_model != profile.name:
        try:
            fallback = load_profile(cfg.fallback_model)
        except ValueError:
            logger.warning("fallback model %r has no profile in config/models.toml; running without a fallback", cfg.fallback_model)

    client = create_client(
        profile,
        count_today=lambda: calls_today(pool),
        daily_call_cap=cfg.daily_call_cap,
        background_enabled=cfg.background_enabled,
        save_span=save_span,
        timeout=cfg.attempt_timeout,
        fallback=fallback,
        fallback_inner=getattr(app.state, "llm_fallback_client", None),
        inner=getattr(app.state, "llm_client", None),      # tests substitute a FakeClient; still gated
    )
    with approved(Grant("user", operation=operation, compile_log_id=compile_log_id)):
        return await client.complete(messages, response_format=response_format, max_tokens=max_tokens, temperature=temperature)


def reply_data(completion: Completion, compile_log_id: int | None) -> dict[str, Any]:
    """How a reply is reported to the panel: the text plus what a reader needs to trust it."""
    return {
        "reply": completion.text,
        "model": completion.model,
        "tokens_used": {"input": completion.usage.input_tokens, "output": completion.usage.output_tokens},
        "latency_ms": completion.latency_ms,
        "compile_log_id": compile_log_id,
        "finish_reason": completion.finish_reason,                 # "length" = the reply was cut off
        "thinking_tokens": completion.usage.thinking_tokens,
        "fell_back_from": completion.fallback_from,                # set if another model answered because this one was busy
    }
