"""The nightly job end to end against the (test) database with a FakeClient and a fixed 2031 clock: the plan for the day that
starts at 05:00, a model failure then a retry, a bad answer re-asked with its violations, a spent quota, reruns being no-ops,
and the 03:00 retry. A real model is never called. Everything created is `zz`-titled or dated 2031 and removed afterwards."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta

import pytest

import storage
from cloud.nightly import run_nightly
from llm.background import background_client
from llm.client import ModelProfile
from llm.errors import LLMError
from llm.fake import FakeClient
from practiced.planning import EXAMPLE_OUTPUT
from schedule_gen.model import IST

NOW = datetime(2031, 3, 4, 2, 0, tzinfo=IST)           # 02:00 on Tuesday: the night run plans the day that starts at 05:00
DAY = date(2031, 3, 4)
PROFILE = ModelProfile(name="fake-gen", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0,
                       supports_tools=True, supports_json=True)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def reply(task_id=7):
    """The prompt's worked example, which was written for a day with a fixed 09:00-10:00 event and one due task. One block is
    marked shallow so the deep work (6 slots) sits inside the budget an empty history gives (4, give or take 2)."""
    out = copy.deepcopy(EXAMPLE_OUTPUT)
    for b in out["plan"]:
        if b.get("task_id") is not None:
            b["task_id"] = task_id
        if b["title"] == "Project coding: tests":
            b["depth"] = "shallow"
    return json.dumps(out)


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
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%' "
                              "OR (after->>'start_at') LIKE '2031-%' OR (before->>'start_at') LIKE '2031-%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%' OR start_at >= '2031-01-01'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM job_run WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM schedule_snapshot WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM day_record WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM practiced_day WHERE day >= '2031-01-01'")
            await cur.execute("DELETE FROM observations WHERE last_evaluated_day >= '2031-01-01'")
            await cur.execute("DELETE FROM review_state WHERE key IN ('latest', 'settings')")
            await cur.execute("DELETE FROM llm_trace WHERE operation IN ('schedule', 'plan') AND model = 'fake-gen'")


slept: list[float] = []


@pytest.fixture
async def pool(monkeypatch):
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    monkeypatch.setenv("AGENT_BACKGROUND_ENABLED", "1")
    slept.clear()

    async def no_wait(seconds):            # the night waits minutes between attempts; a test must not
        slept.append(seconds)
    monkeypatch.setattr("cloud.nightly.SLEEP", no_wait)
    await _clean(p)
    yield p
    await _clean(p)
    await p.close()


def gated(pool, fake):
    return background_client(pool, inner=fake)


async def schedule_rows(pool):
    return await storage.list_schedule_range(pool, start=at(5), end=at(5) + timedelta(days=1))


async def fixed_dentist(pool):
    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(9), end_at=at(10))


async def due_task(pool):
    return await storage.create_task(pool, text="zz report", due_at=at(10) - timedelta(days=1))


async def runs(pool, **kw):
    return {(r["step"], r["attempt"]): r for r in await storage.list_job_runs(pool, day=DAY, **kw)}


async def test_a_night_plans_the_day_around_a_fixed_event_with_a_task_block_and_logs_every_step(pool):
    await fixed_dentist(pool)
    task = await due_task(pool)
    fake = Flaky([reply(task["id"])], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"] == "ok", result.steps
    rows = await schedule_rows(pool)
    generated = [r for r in rows if r["origin"] == "generated"]
    assert len(generated) == len(EXAMPLE_OUTPUT["plan"]) and [r["title"] for r in rows if r["origin"] == "user"] == ["zz Dentist"]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT task_id, why FROM schedule WHERE title = 'Renew passport form' AND start_at >= '2031-01-01'")
            assert await cur.fetchone() == (task["id"], "task due tomorrow, early because it slipped")      # the reason is kept on the row
    prompt = fake.calls[0].messages[1].content
    assert f"[task {task['id']}]" in prompt and "FIXED: zz Dentist" in prompt and "SLOT GRID for the day Tuesday 2031-03-04" in prompt
    logged = {r["step"]: r for r in await storage.list_job_runs(pool, day=DAY)}
    assert logged["plan"]["ok"] and logged["plan"]["detail"]["accepted"] == len(generated) and logged["plan"]["detail"]["reasked"] == 0
    assert result.steps["calendar_pull"].startswith("skipped") and result.steps["extract"] == "ok" and result.steps["digest"] == "ok"
    draft = (await storage.list_drafts(pool, day=DAY))[0]
    assert draft["source"] == "llm" and draft["inputs"]["planner"] and "anchors" in draft["inputs"]["reasoning"]                 # the model's working is kept


async def test_a_rerun_the_same_night_changes_nothing_and_makes_no_second_call(pool):
    await fixed_dentist(pool)
    task = await due_task(pool)
    fake = Flaky([reply(task["id"])], fail=0)
    await run_nightly(pool, now=NOW, client=gated(pool, fake))
    before = len(await schedule_rows(pool))
    again = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert again.steps["plan"] == "skipped: already done" and again.ok
    assert len(fake.calls) == 1 and len(await schedule_rows(pool)) == before


async def test_an_overloaded_model_is_waited_out_between_attempts(pool, monkeypatch):
    from llm.errors import LLMServerError
    from practiced.llmcall import call_json

    quick: list[float] = []

    async def quick_sleep(seconds):                      # the client's own short backoff between its 3 tries
        quick.append(seconds)
    monkeypatch.setitem(call_json.__kwdefaults__, "sleep", quick_sleep)

    class Overloaded(Flaky):
        async def complete(self, *a, **kw):
            self.attempts += 1
            if self.attempts <= self.fail:
                raise LLMServerError("HTTP 503: This model is currently experiencing high demand")
            return await FakeClient.complete(self, *a, **kw)

    await fixed_dentist(pool)
    task = await due_task(pool)
    fake = Overloaded([reply(task["id"])], fail=6)       # two whole attempts (3 quick tries each) fail, the third attempt works
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))
    assert result.steps["plan"] == "ok"
    assert slept == [180, 480]                                         # 3 minutes, then 8 minutes, between the night's attempts
    logged = await runs(pool)
    assert [logged[("plan", n)]["ok"] for n in (1, 2, 3)] == [False, False, True]


async def test_a_spent_quota_or_a_bad_plan_is_not_waited_for(pool):
    await fixed_dentist(pool)
    task = await due_task(pool)
    fake = Flaky([reply(task["id"])], fail=1)                          # a plain LLMError: nothing to wait out
    await run_nightly(pool, now=NOW, client=gated(pool, fake))
    assert slept == []


async def test_a_model_failure_is_retried_once_and_both_attempts_are_logged(pool):
    await fixed_dentist(pool)
    task = await due_task(pool)
    fake = Flaky([reply(task["id"])], fail=1)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"] == "ok" and fake.attempts == 2
    logged = await runs(pool)
    assert logged[("plan", 1)]["ok"] is False and "503" in logged[("plan", 1)]["error"]
    assert logged[("plan", 2)]["ok"] is True


async def test_a_bad_answer_is_re_asked_with_the_violations_listed(pool):
    await fixed_dentist(pool)
    task = await due_task(pool)
    short = json.loads(reply(task["id"]))
    short["plan"].pop()                                          # leaves the last free slot without a block
    fake = Flaky([json.dumps(short), reply(task["id"])], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"] == "ok" and len(fake.calls) == 2
    assert "That plan cannot be used" in fake.calls[1].messages[-1].content and "no block" in fake.calls[1].messages[-1].content
    assert (await runs(pool))[("plan", 1)]["detail"]["reasked"] == 1


async def test_an_answer_that_never_passes_fails_the_step_with_no_fallback_plan(pool):
    await fixed_dentist(pool)
    fake = Flaky([json.dumps({"anchors": {}})], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"].startswith("failed: PlanError") and not result.ok
    assert [r["origin"] for r in await schedule_rows(pool)] == ["user"]            # only the person's own event
    assert result.steps["review"] == "ok"


async def test_three_failures_leave_no_plan_and_a_reason_and_the_run_continues(pool):
    fake = Flaky([reply()], fail=9)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"].startswith("failed") and not result.ok
    assert result.steps["review"] == "ok"                       # the review does not wait for the plan
    assert await schedule_rows(pool) == []
    assert sorted(k for k in await runs(pool) if k[0] == "plan") == [("plan", 1), ("plan", 2), ("plan", 3)]


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

    assert result.steps["plan"].startswith("failed: QuotaExceeded")
    assert fake.attempts == 0 and [k for k in await runs(pool) if k[0] == "plan"] == [("plan", 1)]


async def test_background_tier_off_means_a_clear_failure_not_a_call(pool, monkeypatch):
    monkeypatch.setenv("AGENT_BACKGROUND_ENABLED", "0")
    fake = Flaky([reply()], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert "disabled" in result.steps["plan"] and fake.attempts == 0
    assert result.steps["extract"].startswith("skipped")


async def test_the_three_o_clock_retry_plans_the_day_when_the_night_failed_and_is_a_noop_otherwise(pool):
    retry_at = datetime(2031, 3, 4, 3, 0, tzinfo=IST)
    await fixed_dentist(pool)
    task = await due_task(pool)
    await run_nightly(pool, now=NOW, client=gated(pool, Flaky([reply(task["id"])], fail=9)))      # the night run failed
    assert [r["origin"] for r in await schedule_rows(pool)] == ["user"]

    fake = Flaky([reply(task["id"])], fail=0)
    retry = await run_nightly(pool, kind="morning_retry", now=retry_at, client=gated(pool, fake))
    assert retry.day == DAY and retry.steps["plan"] == "ok" and len(await schedule_rows(pool)) == 1 + len(EXAMPLE_OUTPUT["plan"])
    assert "sessionize" not in retry.steps                                                # only the plan steps

    fake2 = Flaky([reply(task["id"])], fail=0)
    noop = await run_nightly(pool, kind="morning_retry", now=retry_at, client=gated(pool, fake2))
    assert noop.steps["plan"] == "skipped: already done" and fake2.attempts == 0


async def test_a_day_that_already_has_a_generated_plan_is_left_alone(pool):
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO schedule (title, start_at, end_at, origin) VALUES ('zz Earlier plan', %s, %s, 'generated')", (at(11), at(12)))
    fake = Flaky([reply()], fail=0)
    result = await run_nightly(pool, now=NOW, client=gated(pool, fake))

    assert result.steps["plan"] == "skipped: day_already_planned" and fake.attempts == 0
    assert [r["title"] for r in await schedule_rows(pool)] == ["zz Earlier plan"]


async def test_yesterdays_task_check_and_debrief_reach_the_plan_prompt(pool):
    """Sent the way the phone sends them (a tap with a subkind); the night run shows them to the planner on yesterday's blocks."""
    import uuid

    y = DAY - timedelta(days=1)
    ms = lambda d: int(d.timestamp() * 1000)  # noqa: E731
    await storage.create_schedule_entry(pool, title="zz Deep work", start_at=at(14, day=y), end_at=at(16, day=y))
    rows = [
        {"id": str(uuid.uuid4()), "kind": "tap", "at": at(13, 52, day=y).isoformat(), "payload": {
            "subkind": "task_check", "title": "zz Deep work", "status": "blocked", "text": "waiting on API keys", "block_start": ms(at(14, day=y))}},
        {"id": str(uuid.uuid4()), "kind": "tap", "at": at(16, 10, day=y).isoformat(), "payload": {
            "subkind": "debrief", "title": "zz Deep work", "outcome": "continue_tomorrow", "text": "half the tests left", "block_end": ms(at(16, day=y))}},
    ]
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT upload_answers(%s::jsonb)", (json.dumps(rows),))
        await fixed_dentist(pool)
        task = await due_task(pool)
        fake = Flaky([reply(task["id"])] * 3, fail=0)
        result = await run_nightly(pool, now=NOW, client=gated(pool, fake))
        assert result.steps["digest"] == "ok" and result.steps["plan"] == "ok", result.steps
        prompt = next(c.messages[1].content for c in fake.calls if "SLOT GRID" in c.messages[1].content)
        assert 'the person said just before it (task check): blocked / needs info "waiting on API keys"' in prompt
        assert 'the person said after it (debrief): continuing tomorrow "half the tests left"' in prompt
    finally:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM phone_answer WHERE day >= '2031-01-01'")


async def test_the_night_run_reviews_and_snapshots_the_day_that_just_ended_not_the_new_date(pool):
    lived = DAY - timedelta(days=1)
    await storage.insert_job_run(pool, kind="manual", step="review", day=lived, ok=True)    # a daytime run, before the day was over
    task = await due_task(pool)
    await fixed_dentist(pool)
    result = await run_nightly(pool, now=NOW, client=gated(pool, Flaky([reply(task["id"])], fail=0)))

    assert result.steps["review"] == "ok" and result.steps["extract"] == "ok"           # the early review did not count
    back = {r["step"] for r in await storage.list_job_runs(pool, day=lived)}
    assert {"extract", "review"} <= back and not {"extract", "review"} & {r["step"] for r in await storage.list_job_runs(pool, day=DAY)}
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT value->>'date' FROM review_state WHERE key = 'latest'")
            assert (await cur.fetchone())[0] == lived.isoformat()
            await cur.execute("SELECT day FROM schedule_snapshot WHERE day >= '2031-01-01'")
            assert DAY not in {r[0] for r in await cur.fetchall()}        # the new day is photographed after its plan, at 23:55
            await cur.execute("UPDATE job_run SET finished_at = %s WHERE day >= '2031-01-01' AND kind = 'nightly'", (NOW,))  # the fake clock
    again = await run_nightly(pool, now=NOW + timedelta(hours=1), kind="nightly", client=gated(pool, FakeClient([], profile=PROFILE)))
    assert again.steps["review"] == "skipped: already done" and again.steps["extract"] == "skipped: already done"


async def test_the_planner_s_block_type_reaches_the_calendar_row_and_the_phone_rpc(pool):
    """B+C: the phone asks about phone use only in study/build blocks, so it must be told what each block is."""
    await fixed_dentist(pool)
    task = await due_task(pool)
    await run_nightly(pool, now=NOW, client=gated(pool, FakeClient([reply(task["id"])], profile=PROFILE)))
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT title, kind, mode FROM plan_blocks(%s) WHERE origin = 'generated' ORDER BY start_at", (DAY,))
            got = await cur.fetchall()
    kinds = {(b["title"], b["kind"], b["mode"]) for b in EXAMPLE_OUTPUT["plan"]}
    assert len(got) == len(EXAMPLE_OUTPUT["plan"]) and set(got) <= kinds
    assert ("Meditation", "anchor", "rest") in got and ("Walk outside", "break", "outdoor") in got
    assert any(k == "work" and m in ("study", "build") for _, k, m in got)
    by_hand = await storage.create_schedule_entry(pool, title="zz by hand", start_at=at(20), end_at=at(21))
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT kind, mode FROM plan_blocks(%s) WHERE title = 'zz by hand'", (DAY,))
            assert await cur.fetchone() == (None, None)                        # unknown: the phone stays quiet
