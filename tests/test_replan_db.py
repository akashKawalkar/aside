"""Re-plan from the phone, against the test database with a FakeClient and a fixed 2031 clock: the rest of the day is re-planned
from the next half hour around the person's own and edited blocks, Undo puts the old plan back, a plan that never passes changes
nothing, and a stale request expires. Rows are `zz`-titled or dated 2031 and removed afterwards."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

import pytest

import storage
from cloud.nightly import run_nightly
from llm.background import background_client
from llm.client import ModelProfile
from llm.fake import FakeClient
from practiced.planning import slot_range_clock
from practiced.plancheck import CHECKLIST
from schedule_gen.model import IST

DAY = date(2031, 3, 4)
NOW = datetime(2031, 3, 4, 14, 10, tzinfo=IST)          # re-plans from 14:30, slot 19
PROFILE = ModelProfile(name="fake-gen", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0,
                       supports_tools=True, supports_json=True)


def at(h, m=0):
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=IST)


def block(start, slots, kind, title, mode, **kw):
    return {"start_slot": start, "slots": slots, "kind": kind, "title": title, "mode": mode, "why": "test", **kw}


def reply():
    """A valid plan for slots 19-35 around the dentist (slots 22-23)."""
    plan = [
        block(19, 2, "work", "zz Admin", "build", depth="shallow"),
        block(21, 1, "break", "Walk outside", "outdoor", minutes=20),
        block(24, 3, "work", "zz Study", "study", depth="deep"),
        block(27, 1, "break", "Sing songs", "voice", minutes=15),
        block(28, 2, "work", "zz Applications", "build", depth="shallow"),
        block(30, 2, "anchor", "zz Dinner", "rest"),
        block(32, 2, "work", "zz Read", "study", depth="shallow"),
        block(34, 1, "break", "Household chores", "physical", minutes=10),
        block(35, 1, "work", "zz Plan tomorrow", "build", depth="shallow"),
    ]
    return json.dumps({
        "feedback_constraints": ["skip the evening session"],
        "anchors": {"wake_slot": 19, "sleep_slot": 36, "free_slots_left": 13},
        "demand": [{"item": "work", "slots": 9}, {"item": "breaks", "slots": 4}],
        "demand_vs_free": "13 against 13",
        "windows": [{"slots": "24-27", "clock": slot_range_clock(24, 27), "rating": "strong", "evidence": "test"}],
        "placement_notes": "test",
        "checklist": {k: {"ok": True, "fix": ""} for k in CHECKLIST},
        "plan": plan,
    })


async def _clean(pool):
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM replan_request WHERE note LIKE 'zz%%' OR target IN (SELECT id FROM replan_request WHERE note LIKE 'zz%%')")
        await conn.execute("DELETE FROM replan_request WHERE note LIKE 'zz%%'")
        await conn.execute("DELETE FROM schedule_draft WHERE target_day >= '2031-01-01'")
        await conn.execute("DELETE FROM schedule_log WHERE (after->>'start_at') LIKE '2031-%%' OR (before->>'start_at') LIKE '2031-%%'")
        await conn.execute("DELETE FROM schedule WHERE title LIKE 'zz%%' OR start_at >= '2031-01-01'")
        await conn.execute("DELETE FROM job_run WHERE day >= '2031-01-01'")
        await conn.execute("DELETE FROM practiced_day WHERE day >= '2031-01-01'")
        await conn.execute("DELETE FROM llm_trace WHERE operation = 'plan' AND model = 'fake-gen'")


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


async def request(pool, kind="replan", note="zz feeling tired, skip the evening session", target=None, minutes_ago=0):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT request_replan(%s, %s, %s, %s)", (uuid.uuid4(), kind, note, target))
            rid = (await cur.fetchone())[0]
            await cur.execute("UPDATE replan_request SET requested_at = now() - make_interval(mins => %s) WHERE id = %s", (minutes_ago, rid))
    return rid


async def status(pool, rid):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT status, error, result FROM replan_request WHERE id = %s", (rid,))
            return await cur.fetchone()


async def day_rows(pool):
    return {r["title"]: r for r in await storage.list_schedule_range(pool, start=at(5), end=at(5) + timedelta(days=1))}


async def a_planned_day(pool):
    await storage.create_schedule_entry(pool, title="zz Morning", start_at=at(9), end_at=at(11), origin="generated")
    await storage.create_schedule_entry(pool, title="zz Work", start_at=at(14), end_at=at(15), origin="generated")
    await storage.create_schedule_entry(pool, title="zz Evening", start_at=at(18), end_at=at(19), origin="generated")
    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(16), end_at=at(17))
    edited = await storage.create_schedule_entry(pool, title="zz Late call", start_at=at(23), end_at=at(23, 30), origin="generated")
    async with pool.connection() as conn:
        await conn.execute("UPDATE schedule SET edited_by_user = true WHERE id = %s", (edited["id"],))


def run(pool, fake, now=NOW):
    return run_nightly(pool, kind="replan", now=now, client=background_client(pool, inner=fake))


async def test_the_rest_of_the_day_is_re_planned_and_undo_puts_the_old_plan_back(pool):
    await a_planned_day(pool)
    rid = await request(pool)
    fake = FakeClient([reply()], profile=PROFILE)
    result = await run(pool, fake)

    assert result.steps["replan"] == "ok", result.steps
    prompt = fake.calls[0].messages[1].content
    assert "RE-PLAN FROM SLOT 19 (14:30)" in prompt and "FEEDBACK: zz feeling tired, skip the evening session" in prompt
    assert "zz Evening" in prompt                                            # the old evening block is the CURRENT PLAN
    rows = await day_rows(pool)
    assert "zz Evening" not in rows and rows["zz Work"]["end_at"] == at(14, 30)        # removed, and cut short at 14:30
    assert rows["zz Morning"]["end_at"] == at(11) and rows["zz Dentist"]["start_at"] == at(16) and "zz Late call" in rows
    assert rows["zz Admin"]["start_at"] == at(14, 30) and rows["zz Dinner"]["origin"] == "generated"
    st, err, res = await status(pool, rid)
    assert st == "done" and err is None and len(res["added"]) == 9 and res["removed"][0]["title"] == "zz Evening"

    undo_id = await request(pool, kind="undo", note="zz undo", target=rid)
    again = await run(pool, FakeClient([], profile=PROFILE), now=NOW + timedelta(minutes=5))
    assert again.steps["replan"] == "ok", again.steps
    rows = await day_rows(pool)
    assert "zz Admin" not in rows and "zz Dinner" not in rows
    assert rows["zz Evening"]["start_at"] == at(18) and rows["zz Work"]["end_at"] == at(15)
    assert (await status(pool, rid))[0] == "undone" and (await status(pool, undo_id))[0] == "done"


async def test_a_plan_that_never_passes_changes_nothing_and_says_why(pool):
    await a_planned_day(pool)
    rid = await request(pool)
    before = await day_rows(pool)
    bad = json.dumps({"plan": []})
    result = await run(pool, FakeClient([bad] * 9, profile=PROFILE))
    assert result.steps["replan"].startswith("failed")
    st, err, _ = await status(pool, rid)
    assert st == "failed" and "plan rejected" in err
    assert {t: (r["start_at"], r["end_at"]) for t, r in (await day_rows(pool)).items()} == {t: (r["start_at"], r["end_at"]) for t, r in before.items()}


async def test_only_the_newest_request_is_handled_and_a_stale_one_expires(pool):
    old = await request(pool, note="zz old", minutes_ago=45)
    first = await request(pool, note="zz first")
    newest = await request(pool, note="zz newest")
    await a_planned_day(pool)
    fake = FakeClient([reply()], profile=PROFILE)
    await run(pool, fake)
    assert (await status(pool, old))[0] == "expired" and (await status(pool, first))[0] == "superseded"
    assert (await status(pool, newest))[0] == "done" and "FEEDBACK: zz newest" in fake.calls[0].messages[1].content


async def test_with_nothing_pending_the_job_does_nothing(pool):
    result = await run(pool, FakeClient([], profile=PROFILE))
    assert result.steps["replan"] == "skipped: no pending request"


async def test_undo_refuses_an_older_re_plan(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            ids = []
            for n in ("zz one", "zz two"):
                await cur.execute("INSERT INTO replan_request (client_id, kind, note, status, result) VALUES (%s, 'replan', %s, 'done', '{}') RETURNING id",
                                  (uuid.uuid4(), n))
                ids.append((await cur.fetchone())[0])
    await request(pool, kind="undo", note="zz undo", target=ids[0])
    result = await run(pool, FakeClient([], profile=PROFILE))
    assert result.steps["replan"].startswith("failed") and "Only the latest re-plan can be undone" in result.steps["replan"]
