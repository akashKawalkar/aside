# cloud/nightly.py — the nightly brain: ordered, idempotent steps, each leaving a `job_run` row (with the reason on failure).
# Runs from a scheduled GitHub Action (python -m cloud) against the shared database; every step is a plain function that
# already exists for the laptop, so the Action and the laptop cannot drift apart.
#
#   nightly / manual  calendar_pull -> sessionize -> daily -> drop_stale -> extract -> patterns -> generate -> calendar_push -> review
#   morning_retry     calendar_pull -> generate -> calendar_push        (the schedule is for TODAY; a no-op if a draft exists)
#
# A failing step is recorded and the run goes on to the next one (the schedule does not wait for the review). The schedule
# step is tried `[cloud] retry_attempts` times on model errors; a missing quota or a disabled background tier is not retried.
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import storage
from config import load_config
from context.recipe import load_recipe
from extractor.worker import run_extractor_once
from llm.approved import ApprovalRequired, QuotaExceeded
from llm.background import background_client
from llm.client import load_profile
from patterns.engine import run_patterns_once
from review.daily import run_daily_once
from review.store import DbBackend, ReviewStore
from review.wiring import make_review_generator
from schedule_gen import service
from schedule_gen.model import rules_from_config, tomorrow
from sessions.job import sessionize_recent

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

FULL = ("calendar_pull", "sessionize", "daily", "drop_stale", "extract", "patterns", "generate", "calendar_push", "review")
STEPS = {"nightly": FULL, "manual": FULL, "morning_retry": ("calendar_pull", "generate", "calendar_push")}
ONCE = {"extract", "generate", "review"}      # once one succeeded for its day, a rerun skips it (no second model call)
NOT_RETRIED = (QuotaExceeded, ApprovalRequired)

Hook = Callable[[datetime], Awaitable[dict[str, Any] | None]]


@dataclass
class Hooks:
    """Where Google Calendar / Tasks sync plugs in (step 8). Absent hooks are recorded as skipped."""
    pull: Hook | None = None
    push: Hook | None = None


@dataclass
class NightlyResult:
    kind: str
    day: date                                  # the day the schedule step was for
    steps: dict[str, str] = field(default_factory=dict)      # step -> "ok" | "skipped: why" | "failed: why"

    @property
    def ok(self) -> bool:
        return not any(v.startswith("failed") for v in self.steps.values())


def target_day(kind: str, now: datetime) -> date:
    """The schedule being made: tomorrow's for the evening run, today's for the morning retry."""
    return now.astimezone(IST).date() if kind == "morning_retry" else tomorrow(now)


async def run_nightly(
    pool, *, kind: str = "nightly", now: datetime | None = None, client=None, hooks: Hooks | None = None,
) -> NightlyResult:
    """`client` substitutes a gated client (tests pass one around a FakeClient); by default one is built for the real model."""
    if kind not in STEPS:
        raise ValueError(f"kind must be one of {', '.join(STEPS)}")
    now = (now or datetime.now(IST)).astimezone(IST)
    today, cfg, hooks = now.date(), load_config(), hooks or Hooks()
    result = NightlyResult(kind=kind, day=target_day(kind, now))
    generator = make_review_generator(pool)

    async def record(step: str, day: date, ok: bool, *, attempt: int = 1, error: str | None = None, detail: dict | None = None) -> None:
        try:
            await storage.insert_job_run(pool, kind=kind, step=step, day=day, ok=ok, attempt=attempt, error=error, detail=detail)
        except Exception:          # a broken log must not stop the night's work
            log.exception("could not write job_run for %s", step)

    async def step_sessionize() -> dict:
        out = await sessionize_recent(pool, now=now)
        return {"days": out.days, "created": out.created}

    async def step_daily() -> dict:
        # The laptop writes today's snapshot after 23:55; the job runs earlier, so take it now (a later pull may refine it).
        dq = cfg.data_quality.model_copy(update={"snapshot_time": "00:00"})
        written = await run_daily_once(pool, generator, dq, now=now)
        return {k: [d.isoformat() for d in v] for k, v in written.items()}

    async def step_drop_stale() -> dict:
        return {"dropped": [t["id"] for t in await storage.drop_stale_tasks(pool, now=now)]}

    async def step_extract() -> dict:
        if not cfg.llm.background_enabled:
            return {"skipped": "background model calls are disabled"}
        return {"items": await run_extractor_once(pool, True, now, day=today, client=client or background_client(pool))}

    async def step_patterns() -> dict:
        return {"observations": await run_patterns_once(pool, cfg.patterns, now=now)}

    async def step_review() -> dict:
        store = ReviewStore(DbBackend(pool))
        review = await generator.generate(review_date=today)
        await store.save_latest(review)
        return {"date": today.isoformat()}

    async def step_generate() -> dict:
        async def save_log(row):
            try:
                return await storage.insert_compile_log(pool, row)
            except Exception:
                log.warning("could not save compile log")
                return None

        return await service.generate_unattended(
            pool, client or background_client(pool), now=now, rules=rules_from_config(result.day, cfg.data_quality),
            profile=load_profile(), recipe=load_recipe("schedule"), save_log=save_log, max_item_tokens=cfg.llm.max_item_tokens,
        )

    async def step_hook(hook: Hook | None) -> dict:
        return {"skipped": "not configured"} if hook is None else (await hook(now) or {})

    actions: dict[str, Callable[[], Awaitable[dict]]] = {
        "calendar_pull": lambda: step_hook(hooks.pull), "sessionize": step_sessionize, "daily": step_daily,
        "drop_stale": step_drop_stale, "extract": step_extract, "patterns": step_patterns, "generate": step_generate,
        "calendar_push": lambda: step_hook(hooks.push), "review": step_review,
    }

    for step in STEPS[kind]:
        day = result.day if step == "generate" else today
        if step in ONCE and await storage.step_done(pool, day=day, step=step):
            result.steps[step] = "skipped: already done"
            continue

        attempts = cfg.cloud.retry_attempts if step == "generate" else 1
        for attempt in range(1, attempts + 1):
            try:
                detail = await actions[step]()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                log.warning("%s failed (attempt %d/%d): %s", step, attempt, attempts, message)
                await record(step, day, False, attempt=attempt, error=message)
                result.steps[step] = f"failed: {message}"
                if isinstance(exc, NOT_RETRIED):
                    break
                continue
            skipped = detail.get("skipped")
            await record(step, day, True, attempt=attempt, detail=detail)
            result.steps[step] = f"skipped: {skipped}" if skipped else "ok"
            break

    return result
