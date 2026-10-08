"""The nightly job end to end against the (test) database with a FakeClient and a fixed 2031 clock: success, a model failure
then a retry, a spent quota, reruns being no-ops, and the morning retry. A real model is never called. Everything created
is `zz`-titled or dated 2031 and removed afterwards."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

import storage
from cloud.nightly import run_nightly
from llm.background import background_client
from llm.client import ModelProfile
from llm.errors import LLMError
from llm.fake import FakeClient
from schedule_gen.model import IST

NOW = datetime(2031, 3, 3, 21, 0, tzinfo=IST)          # Monday evening; the job makes Tuesday 4 March 2031
DAY = date(2031, 3, 4)
PROFILE = ModelProfile(name="fake-gen", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0,
                       supports_tools=True, supports_json=True)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def reply(task_id=None):
    return json.dumps({"entries": [
        {"title": "zz Write report", "start": "09:00", "end": "11:00", "reason": "due soon", "task_id": task_id},
        {"title": "zz Gym", "start": "18:00", "end": "19:00", "reason": "routine"}]})


class Flaky(FakeClient):
    """Fails the first `fail` calls the way an overloaded provider does."""

    def __init__(self, script, fail=1):
        super().__init__(script, profile=PROFILE)
        self.fail, self.attempts = fail, 0

    async def complete(self, *a, **kw):
        self.attempts += 1
        if self.attempts <= self.fail:
            raise LLMError("503 high demand")
        return await super().complete(*a, **kw)


async def _clean(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM schedule_draft WHERE target_day >= '2031-01-01'")
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM job_run WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM schedule_snapshot WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM day_record WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM observations WHERE last_evaluated_day >= '2031-01-01'")
            await cur.execute("DELETE FROM review_state WHERE key IN ('latest', 'settings')")
            await cur.execute("DELETE FROM llm_trace WHERE operation = 'schedule' AND model = 'fake-gen'")


@pytest.fixture
async def pool(monkeypatch):
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    monkeypatch.setenv("AGENT_BACKGROUND_ENABLED", "1")
    await _clean(p)
    yield p
    await _clean(p)
    await p.close()


def gated(pool, fake):
    return background_client(pool, inner=fake)


async def schedule_rows(pool):
    return await storage.list_schedule_range(pool, start=at(0), end=at(0) + timedelta(days=1))


async def runs(pool, **kw):
    return {(r["step"], r["attempt"]): r for r in await storage.list_job_runs(pool, day=DAY, **kw)}


async def test_a_night_makes_a_generated_schedule_with_a_task_block_and_logs_every_step(pool):
    task = await storage.create_task(pool, text="zz report", due_at=at(10) - timedelta(days=1))
    fake = Flaky([reply(task["id"])], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["generate"] == "ok", result.steps
    rows = await schedule_rows(pool)
    assert [(r["title"], r["origin"]) for r in rows] == [("zz Write report", "generated"), ("zz Gym", "generated")]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT task_id FROM schedule WHERE title = 'zz Write report'")
            assert await cur.fetchone() == (task["id"],)
    assert "[task" in fake.calls[0].messages[1].content                         # the tasks were offered with their ids
    logged = {r["step"]: r for r in await storage.list_job_runs(pool, day=DAY)}
    assert logged["generate"]["ok"] and logged["generate"]["detail"]["accepted"] == 2
    assert result.steps["calendar_pull"].startswith("skipped") and result.steps["extract"] == "ok"


async def test_a_rerun_the_same_night_changes_nothing_and_makes_no_second_call(pool):
    fake = Flaky([reply()], fail=0)
    await run_nightly(pool, now=NOW, client=gated(pool, fake))
    before = len(await schedule_rows(pool))
    again = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert again.steps["generate"] == "skipped: already done" and again.ok
    assert len(fake.calls) == 1 and len(await schedule_rows(pool)) == before


async def test_a_model_failure_is_retried_once_and_both_attempts_are_logged(pool):
    fake = Flaky([reply()], fail=1)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["generate"] == "ok" and fake.attempts == 2
    logged = await runs(pool)
    assert logged[("generate", 1)]["ok"] is False and "503" in logged[("generate", 1)]["error"]
    assert logged[("generate", 2)]["ok"] is True and len(await schedule_rows(pool)) == 2


async def test_two_failures_leave_no_schedule_and_a_reason_and_the_run_continues(pool):
    fake = Flaky([reply()], fail=5)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["generate"].startswith("failed") and not result.ok
    assert result.steps["review"] == "ok"                       # the review does not wait for the schedule
    assert await schedule_rows(pool) == []
    logged = await runs(pool)
    assert sorted(k for k in logged if k[0] == "generate") == [("generate", 1), ("generate", 2)]


async def test_a_spent_quota_is_not_retried(pool, monkeypatch):
    import config
    real = config.load_config

    def capped():
        cfg = real()
        cfg.llm.daily_call_cap = 0
        return cfg

    monkeypatch.setattr("llm.background.load_config", capped)
    fake = Flaky([reply()], fail=0)
    result = await run_nightly(pool, now=NOW, client=background_client(pool, inner=fake))

    assert result.steps["generate"].startswith("failed: QuotaExceeded")
    assert fake.attempts == 0 and [k for k in await runs(pool) if k[0] == "generate"] == [("generate", 1)]


async def test_background_tier_off_means_a_clear_failure_not_a_call(pool, monkeypatch):
    monkeypatch.setenv("AGENT_BACKGROUND_ENABLED", "0")
    fake = Flaky([reply()], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert "disabled" in result.steps["generate"] and fake.attempts == 0
    assert result.steps["extract"].startswith("skipped")


async def test_the_morning_retry_fills_today_when_the_night_failed_and_is_a_noop_otherwise(pool):
    morning = datetime(2031, 3, 4, 7, 30, tzinfo=IST)
    await run_nightly(pool, now=NOW, client=gated(pool, Flaky([reply()], fail=5)))      # night failed: nothing for the 4th
    assert await schedule_rows(pool) == []

    fake = Flaky([reply()], fail=0)
    retry = await run_nightly(pool, kind="morning_retry", now=morning, client=gated(pool, fake))
    assert retry.day == DAY and retry.steps["generate"] == "ok" and len(await schedule_rows(pool)) == 2
    assert "sessionize" not in retry.steps                                                # only the schedule steps

    fake2 = Flaky([reply()], fail=0)
    noop = await run_nightly(pool, kind="morning_retry", now=morning, client=gated(pool, fake2))
    assert noop.steps["generate"] == "skipped: already done" and fake2.attempts == 0


async def test_a_day_the_user_already_built_is_left_alone(pool):
    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(15), end_at=at(16))
    fake = Flaky([reply()], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["generate"] == "skipped: tomorrow_has_entries" and fake.attempts == 0
    assert [r["title"] for r in await schedule_rows(pool)] == ["zz Dentist"]
