# llm/background.py — the gated client an unattended job uses. Same chokepoint as everything else (llm/factory.create_client):
# it only runs inside a background grant, only when [llm] background_enabled, and only under the daily cap.
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import load_config
from llm.client import LLMClient, load_profile
from llm.factory import create_client
from llm.quota import provider_day_start
from storage import count_llm_traces_since, insert_llm_trace

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def background_client(pool, *, inner: LLMClient | None = None, fallback_inner: LLMClient | None = None):
    """`inner` / `fallback_inner` substitute already-built clients (tests pass FakeClients); they are still gated."""
    cfg = load_config().llm

    async def save_span(row):
        try:
            await insert_llm_trace(pool, row)
        except Exception:
            log.warning("Could not persist LLM trace to database")

    fallback = None
    if inner is None and cfg.fallback_model:
        try:
            fallback = load_profile(cfg.fallback_model)
        except ValueError:
            log.warning("fallback model %r has no profile; running without a fallback", cfg.fallback_model)

    return create_client(
        count_today=lambda: count_llm_traces_since(pool, provider_day_start(datetime.now(IST))),
        daily_call_cap=cfg.daily_call_cap, background_enabled=cfg.background_enabled, save_span=save_span,
        timeout=cfg.attempt_timeout, fallback=fallback, inner=inner, fallback_inner=fallback_inner,
    )
