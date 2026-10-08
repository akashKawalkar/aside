# extractor/worker.py — the nightly extraction pass: one batched background call for yesterday's statements.
# Background tier (plan §3.9): runs only when [llm] background_enabled is true, inside a background grant, under the
# daily cap, with the privacy layer applied to what it reads.
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from config import load_config
from extractor.job import extract_for_day
from llm.approved import ApprovalRequired, Grant, QuotaExceeded, approved
from llm.factory import create_client
from llm.quota import provider_day_start
from storage import count_llm_traces_since, insert_llm_trace
from storage.database import AsyncConnectionPool

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
RUN_HOUR = 2        # IST; yesterday is complete by then


async def run_extractor_once(
    pool: AsyncConnectionPool, background_enabled: bool, now: datetime | None = None, *, day: date | None = None, client=None,
) -> int:
    """Extract candidate items for `day` (default: yesterday, IST). The nightly job passes today, so a journal typed at
    20:45 counts. `client` is a gated client built by the caller (tests, the nightly job). Returns how many were stored;
    0 when background calls are off."""
    if not background_enabled:
        return 0
    now = (now or datetime.now(IST)).astimezone(IST)
    day = day or (now - timedelta(days=1)).date()
    cfg = load_config().llm
    if client is not None:
        with approved(Grant("background", operation="extraction")):
            return len(await extract_for_day(client, pool, day))

    async def save_span(row):
        try:
            await insert_llm_trace(pool, row)
        except Exception:
            log.warning("Could not persist LLM trace to database")

    client = create_client(
        count_today=lambda: count_llm_traces_since(pool, provider_day_start(datetime.now(IST))),
        daily_call_cap=cfg.daily_call_cap, background_enabled=True, save_span=save_span, timeout=cfg.attempt_timeout,
    )
    with approved(Grant("background", operation="extraction")):
        items = await extract_for_day(client, pool, day)
    return len(items)


async def run_extractor_worker(pool: AsyncConnectionPool, interval: float = 600.0) -> None:
    """Checks every 10 minutes; at most one attempt per IST day (a failed attempt is not retried: it may have used quota)."""
    attempted: date | None = None
    while True:
        try:
            now = datetime.now(IST)
            if now.hour >= RUN_HOUR and attempted != now.date() and load_config().llm.background_enabled:
                attempted = now.date()
                count = await run_extractor_once(pool, True, now=now)
                if count:
                    log.info("extractor stored %d candidate items for yesterday", count)
        except asyncio.CancelledError:
            raise
        except (QuotaExceeded, ApprovalRequired) as exc:
            log.info("extractor skipped: %s", exc)
        except Exception:
            log.exception("extractor worker failed")
        await asyncio.sleep(interval)
