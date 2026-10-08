"""Schedule generation end to end against the real database, with a FakeClient and a fixed clock in 2031 so no real day
is touched. Everything these tests create is titled/worded `zz...` or dated 2031 and removed afterwards. Skipped if the
database is unreachable. A real model is never called."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

import storage
from capture.server import create_app
from llm.client import ModelProfile
from llm.fake import FakeClient
from schedule_gen.model import IST

NOW = datetime(2031, 3, 3, 12, 0, tzinfo=IST)          # a Monday; "tomorrow" is Tuesday 4 March 2031
DAY = date(2031, 3, 4)
LAST_TUESDAY = date(2031, 2, 25)
PROFILE = ModelProfile(name="fake-gen", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0,
                       supports_tools=True, supports_json=True)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def reply(*entries):
    return json.dumps({"entries": [{"title": t, "start": s, "end": e, "reason": r} for t, s, e, r in entries]})


GOOD = reply(("zz Gym", "07:00", "08:00", "your usual routine"), ("zz Deep work", "09:00", "12:00", "weekday focus block"),
             ("zz Tennis", "18:30", "20:00", "Tuesday evening"), ("zz Midnight", "23:30", "23:45", "too late"))


class Env:
    def __init__(self, monkeypatch, pool, script):
        import capture.llm_run as routes

        self.pool = pool
        self.traces: list[dict] = []
        self.used_today = 0

        async def save_trace(p, row): self.traces.append(row)
        async def count(p): return self.used_today + len(self.traces)
        async def save_compile(p, row): return None
        monkeypatch.setattr(routes, "insert_llm_trace", save_trace)
        monkeypatch.setattr(routes, "calls_today", count)
        monkeypatch.setattr(storage, "insert_compile_log", save_compile)

        self.client = FakeClient(script, profile=PROFILE)
        self.app = create_app()
        self.app.state.db_pool = pool
        self.app.state.llm_profile = PROFILE
        self.app.state.llm_client = self.client
        self.app.state.now_fn = lambda: NOW
        self.http = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def get(self, path): return (await self.http.get(path)).json()
    async def post(self, path, body=None): return (await self.http.post(path, json=body or {})).json()
    async def patch(self, path, body): return (await self.http.patch(path, json=body)).json()

    async def generate(self, instruction=""):
        """Ask for a draft: the model is called straight away and the new draft comes back."""
        out = await self.post("/schedule/draft/generate", {"instruction": instruction})
        assert out["status"] == "ok", out
        return out["data"]["draft"]

    async def draft_via_llm(self, instruction=""):
        return await self.generate(instruction)

    def prompt(self, n=-1):
        """(system, user) of the n-th model call."""
        return tuple(m.content for m in self.client.calls[n].messages)

    async def schedule_rows(self):
        return await storage.list_schedule_range(self.pool, start=at(0), end=at(0) + timedelta(days=1))


async def _clean(pool):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM schedule_draft WHERE target_day = %s", (DAY,))
            await cur.execute("DELETE FROM schedule_log WHERE after->>'title' LIKE 'zz%' OR before->>'title' LIKE 'zz%'")
            await cur.execute("DELETE FROM schedule WHERE title LIKE 'zz%'")
            await cur.execute("DELETE FROM instruction_records WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM candidate_items WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    await _clean(p)
    yield p
    await _clean(p)
    await p.close()


@pytest.fixture
async def with_template(pool):
    """The user's last Tuesday: a workout and a focus block, both made by the user."""
    await storage.create_schedule_entry(pool, title="zz Gym", start_at=at(7, day=LAST_TUESDAY), end_at=at(8, day=LAST_TUESDAY))
    await storage.create_schedule_entry(pool, title="zz Deep work", start_at=at(9, day=LAST_TUESDAY), end_at=at(12, day=LAST_TUESDAY))


def make_env(monkeypatch, pool, *script):
    return Env(monkeypatch, pool, list(script) or [GOOD])


# ---------- can the user ask? ----------

async def test_status_tracks_whether_tomorrow_is_empty_or_has_a_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    state = (await env.get("/schedule/draft/status"))["data"]
    assert (state["day"], state["can_generate"], state["reason"], state["draft"], state["llm_available"]) == ("2031-03-04", True, None, None, True)

    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(15), end_at=at(16))
    state = (await env.get("/schedule/draft/status"))["data"]
    assert (state["can_generate"], state["reason"]) == (False, "tomorrow_has_entries")

    refused = await env.post("/schedule/draft/generate")
    assert refused["status"] == "error" and "already has entries" in refused["message"]


async def test_the_button_is_off_while_a_draft_is_open_and_returns_when_it_is_discarded(monkeypatch, pool, with_template):
    env = make_env(monkeypatch, pool)
    draft = (await env.post("/schedule/draft/placeholder"))["data"]["draft"]
    state = (await env.get("/schedule/draft/status"))["data"]
    assert (state["can_generate"], state["reason"], state["draft"]["id"]) == (False, "draft_open", draft["id"])
    assert (await env.post("/schedule/draft/generate"))["status"] == "error"

    assert (await env.post(f"/schedule/draft/{draft['id']}/discard"))["data"]["draft"]["status"] == "discarded"
    assert (await env.get("/schedule/draft/status"))["data"]["can_generate"] is True
    assert await env.schedule_rows() == []                     # discarding writes nothing to the schedule


# ---------- the rule-based draft ----------

async def test_the_placeholder_copies_last_tuesday_without_a_model_call(monkeypatch, pool, with_template):
    env = make_env(monkeypatch, pool)
    draft = (await env.post("/schedule/draft/placeholder"))["data"]["draft"]
    assert (draft["source"], draft["version"], draft["status"], draft["model"]) == ("placeholder", 1, "open", None)
    assert [(e["title"], e["state"], e["reason"]) for e in draft["entries"]] == [
        ("zz Gym", "pending", "Copied from Tue 25 Feb"), ("zz Deep work", "pending", "Copied from Tue 25 Feb")]
    assert draft["entries"][0]["start_at"].startswith("2031-03-04T07:00")
    assert env.client.calls == [] and env.traces == []


async def test_the_placeholder_only_copies_what_the_user_stood_behind(monkeypatch, pool):
    mine = await storage.create_schedule_entry(pool, title="zz Mine", start_at=at(9, day=LAST_TUESDAY), end_at=at(10, day=LAST_TUESDAY))
    await storage.create_schedule_entry(pool, title="zz Generated", start_at=at(11, day=LAST_TUESDAY), end_at=at(12, day=LAST_TUESDAY), origin="generated")
    env = make_env(monkeypatch, pool)
    entries = (await env.post("/schedule/draft/placeholder"))["data"]["draft"]["entries"]
    assert [e["title"] for e in entries] == ["zz Mine"] and mine["origin"] == "user"


async def test_the_placeholder_with_nothing_to_copy_is_an_empty_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = (await env.post("/schedule/draft/placeholder"))["data"]["draft"]
    assert draft["entries"] == [] and draft["inputs"]["template_day"] is None


# ---------- generate: ask, parse, store ----------

async def test_generate_sends_the_compiled_prompt_and_returns_a_draft(monkeypatch, pool, with_template):
    env = make_env(monkeypatch, pool)
    draft = await env.generate("zz tomorrow is ekadashi")
    assert draft["status"] == "open" and len(env.client.calls) == 1
    system, user = env.prompt()
    assert [m.role for m in env.client.calls[0].messages] == ["system", "user"]
    assert "Tuesday 04 March 2031" in system and "07:00" in system and "Do NOT add meals" in system
    assert "zz tomorrow is ekadashi" in user and "07:00-08:00 zz Gym" in user and "Tuesday 25 Feb" in user
    assert "free slots" in user and "Draft the schedule for Tuesday 04 March 2031" in user


async def test_the_instruction_is_kept_as_a_record_scoped_to_tomorrow(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    await env.generate("zz keep the evening free")
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT valid_from, valid_until FROM instruction_records WHERE text = 'zz keep the evening free'")
            assert await cur.fetchall() == [(DAY, DAY)]


async def test_the_first_draft_keeps_what_the_validator_rejected(monkeypatch, pool, with_template):
    env = make_env(monkeypatch, pool)
    draft = await env.generate()

    assert (draft["version"], draft["status"], draft["source"], draft["model"]) == (1, "open", "llm", "fake-gen")
    assert [e["title"] for e in draft["entries"]] == ["zz Gym", "zz Deep work", "zz Tennis"]
    assert all(e["state"] == "pending" and not e["locked"] for e in draft["entries"])
    assert [(r["entry"]["title"], r["reason"]) for r in draft["rejected"]] == [("zz Midnight", "outside_waking_hours")]
    assert draft["entries"][0]["reason"] == "your usual routine"

    call = env.client.calls[0]
    assert call.response_format == {"type": "json_object"} and call.max_tokens == 3000
    (trace,) = env.traces
    assert trace["operation"] == "schedule" and trace["error"] is None
    assert await env.schedule_rows() == []                      # a draft is not a schedule: nothing written until accepted


async def test_the_first_proposal_is_kept_unchanged_whatever_happens_to_the_working_copy(monkeypatch, pool, with_template):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    original = json.loads(json.dumps(draft["entries"]))
    await env.patch(f"/schedule/draft/{draft['id']}/entries/0", {"title": "zz Gym (moved)", "start_at": at(8).isoformat(), "end_at": at(9).isoformat()})
    await env.post(f"/schedule/draft/{draft['id']}/accept", {"indexes": [1]})
    await env.post(f"/schedule/draft/{draft['id']}/discard", {"indexes": [2]})

    stored = (await env.get(f"/schedule/draft/{draft['id']}"))["data"]["draft"]
    assert [{k: v for k, v in e.items() if k != "index"} for e in stored["proposed"]] == [{k: v for k, v in e.items() if k != "index"} for e in original]
    assert stored["entries"][0]["title"] == "zz Gym (moved)" and stored["entries"][1]["state"] == "accepted"


async def test_an_unusable_reply_spends_the_call_but_creates_no_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool, "Sorry, I cannot plan that.")
    out = await env.post("/schedule/draft/generate")
    assert out["status"] == "error" and "no JSON" in out["message"]
    assert (await env.get("/schedule/draft/status"))["data"]["draft"] is None
    assert len(env.traces) == 1                                 # the call happened and is counted


async def test_a_reply_that_proposes_nothing_is_a_valid_empty_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool, '{"entries": []}')
    draft = await env.draft_via_llm()
    assert draft["entries"] == [] and draft["status"] == "open"


async def test_proposals_cannot_overlap_something_already_on_the_day(monkeypatch, pool):
    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(9, 30), end_at=at(10, 30))
    env = make_env(monkeypatch, pool)
    # the day is not empty, so generation is off; the placeholder and the finalizer must still respect fixed blocks
    from schedule_gen import service
    from schedule_gen.model import rules_from_config
    from config import load_config
    from llm.client import Completion
    rules = rules_from_config(DAY, load_config().data_quality)
    call = type("Call", (), {"meta": {"instruction": "", "inputs": {}}, "compile_log_id": None})()
    result = await service.finalize_generation(pool, call, Completion(GOOD, "fake-gen"), rules)
    titles = {e["title"] for e in result["draft"]["entries"]}
    assert "zz Deep work" not in titles and {"zz Gym", "zz Tennis"} <= titles
    assert any(r["reason"] == "overlaps_fixed:zz Dentist" for r in result["draft"]["rejected"])


async def test_generation_is_refused_when_the_daily_cap_is_used_up_but_the_placeholder_still_works(monkeypatch, pool, with_template):
    from config import load_config
    env = make_env(monkeypatch, pool)
    env.used_today = load_config().llm.daily_call_cap
    refused = await env.post("/schedule/draft/generate")
    assert refused["status"] == "error" and "daily LLM call cap" in refused["message"]
    assert (await env.get("/schedule/draft/status"))["data"]["llm_available"] is False
    assert (await env.post("/schedule/draft/placeholder"))["status"] == "ok"


# ---------- editing and accepting ----------

async def test_editing_an_entry_checks_it_and_locks_it(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    path = f"/schedule/draft/{draft['id']}/entries"

    ok = (await env.patch(f"{path}/2", {"title": "zz Tennis!", "start_at": at(19).isoformat(), "end_at": at(20, 30).isoformat()}))["data"]["draft"]
    edited = ok["entries"][2]
    assert (edited["title"], edited["locked"], edited["edited"]) == ("zz Tennis!", True, True) and edited["start_at"].startswith("2031-03-04T19:00")

    assert "overlaps" in (await env.patch(f"{path}/0", {"start_at": at(8, 30).isoformat(), "end_at": at(9, 30).isoformat()}))["message"]
    assert "waking hours" in (await env.patch(f"{path}/0", {"start_at": at(3).isoformat(), "end_at": at(4).isoformat()}))["message"]
    assert "after the start" in (await env.patch(f"{path}/0", {"start_at": at(9).isoformat(), "end_at": at(8).isoformat()}))["message"]
    assert "UTC offset" in (await env.patch(f"{path}/0", {"start_at": "2031-03-04T09:00:00"}))["message"]
    assert (await env.patch(f"{path}/9", {"title": "x"}))["status"] == "error"
    assert (await env.http.patch(f"/schedule/draft/{draft['id']}/entries/0", json={"surprise": 1})).status_code == 422   # unknown field


async def test_accepting_writes_generated_schedule_rows_and_logs_them(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    out = await env.post(f"/schedule/draft/{draft['id']}/accept", {"indexes": [0, 1]})
    assert out["status"] == "ok" and len(out["data"]["accepted"]) == 2 and out["data"]["conflicts"] == []

    rows = await env.schedule_rows()
    assert [(r["title"], r["origin"], r["edited_by_user"]) for r in rows] == [("zz Gym", "generated", False), ("zz Deep work", "generated", False)]
    saved = out["data"]["draft"]
    assert saved["status"] == "open"                            # one entry is still pending
    assert [(e["state"], e["locked"], e["schedule_id"] is not None) for e in saved["entries"]] == [
        ("accepted", True, True), ("accepted", True, True), ("pending", False, False)]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT action FROM schedule_log WHERE after->>'title' = 'zz Gym'")
            assert await cur.fetchall() == [("create",)]


async def test_accepting_everything_closes_the_draft_and_turns_the_button_off_for_good(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    out = await env.post(f"/schedule/draft/{draft['id']}/accept")          # no indexes = every pending entry
    assert out["data"]["draft"]["status"] == "accepted" and len(out["data"]["accepted"]) == 3
    state = (await env.get("/schedule/draft/status"))["data"]
    assert (state["can_generate"], state["reason"], state["draft"]) == (False, "tomorrow_has_entries", None)
    assert (await env.post(f"/schedule/draft/{draft['id']}/accept"))["status"] == "error"   # no longer open


async def test_editing_a_generated_entry_afterwards_marks_it_edited_by_the_user(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    out = await env.post(f"/schedule/draft/{draft['id']}/accept", {"indexes": [0]})
    row = (await env.schedule_rows())[0]
    await env.http.patch(f"/schedule/{row['id']}", json={"title": "zz Gym", "start_at": at(7, 30).isoformat(), "end_at": at(8, 30).isoformat()})
    assert (await env.schedule_rows())[0]["edited_by_user"] is True


async def test_an_entry_that_clashes_with_something_added_since_stays_pending(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    await storage.create_schedule_entry(pool, title="zz Surprise", start_at=at(9, 30), end_at=at(10, 0))   # added after the draft
    out = await env.post(f"/schedule/draft/{draft['id']}/accept")
    assert out["data"]["conflicts"] == [1] and len(out["data"]["accepted"]) == 2
    assert out["data"]["draft"]["entries"][1]["state"] == "pending" and out["data"]["draft"]["status"] == "open"
    assert "clashed" in out["message"]


async def test_discarding_some_entries_then_the_rest_closes_the_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    part = (await env.post(f"/schedule/draft/{draft['id']}/discard", {"indexes": [0, 1]}))["data"]["draft"]
    assert part["status"] == "open" and [e["state"] for e in part["entries"]] == ["discarded", "discarded", "pending"]
    done = (await env.post(f"/schedule/draft/{draft['id']}/discard", {"indexes": [2]}))["data"]["draft"]
    assert done["status"] == "discarded" and await env.schedule_rows() == []


# ---------- revising ----------

async def test_a_revision_keeps_accepted_and_edited_entries_and_redoes_the_rest(monkeypatch, pool, with_template):
    revised = reply(("zz Reading", "19:30", "20:30", "you asked for a quieter evening"))
    env = make_env(monkeypatch, pool, GOOD, revised)
    first = await env.draft_via_llm()
    base = f"/schedule/draft/{first['id']}"
    await env.post(f"{base}/accept", {"indexes": [0]})                                              # Gym: accepted, now a schedule row
    await env.patch(f"{base}/entries/1", {"title": "zz Deep work", "start_at": at(9, 30).isoformat(), "end_at": at(12).isoformat()})  # locked
    # entry 2 (Tennis) is untouched: the model may redo it

    second = (await env.post(f"{base}/revise", {"instruction": "zz make the evening quieter"}))["data"]["draft"]
    user = env.prompt()[1]
    assert "Revise the schedule" in user and "zz make the evening quieter" in user
    assert "07:00-08:00 zz Gym" in user and "09:30-12:00 zz Deep work" in user        # both are fixed blocks now
    assert "18:30-20:00 zz Tennis" in user                                            # the only entry the model may change

    assert second["version"] == 2 and second["status"] == "open" and second["parent_id"] == first["id"]
    assert [(e["title"], e["state"], e["locked"]) for e in second["entries"]] == [
        ("zz Gym", "accepted", True), ("zz Deep work", "pending", True), ("zz Reading", "pending", False)]

    versions = (await env.get("/schedule/drafts?day=2031-03-04"))["data"]["drafts"]
    assert [(d["version"], d["status"]) for d in versions] == [(2, "open"), (1, "superseded")]
    assert [e["title"] for e in versions[1]["proposed"]] == ["zz Gym", "zz Deep work", "zz Tennis"]   # version 1 still says what was first proposed


async def test_a_revision_cannot_move_a_locked_entry_because_it_is_not_the_models_to_move(monkeypatch, pool):
    clash = reply(("zz Gym 2", "07:30", "08:30", "overlaps the locked gym"))
    env = make_env(monkeypatch, pool, GOOD, clash)
    first = await env.draft_via_llm()
    await env.post(f"/schedule/draft/{first['id']}/accept", {"indexes": [0]})
    second = (await env.post(f"/schedule/draft/{first['id']}/revise", {"instruction": "zz another workout"}))["data"]["draft"]
    assert [e["title"] for e in second["entries"]] == ["zz Gym"]
    assert second["rejected"][0]["reason"] == "overlaps_fixed:zz Gym"


async def test_only_an_open_draft_can_be_revised(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    await env.post(f"/schedule/draft/{draft['id']}/discard")
    assert (await env.post(f"/schedule/draft/{draft['id']}/revise", {"instruction": "zz again"}))["status"] == "error"
    assert (await env.post("/schedule/draft/99999999/revise", {"instruction": "zz again"}))["status"] == "error"
    assert (await env.post(f"/schedule/draft/{draft['id']}/revise", {"instruction": ""})).get("status") != "ok"


# ---------- context ----------

async def test_the_prompt_carries_tasks_candidate_facts_and_free_time(monkeypatch, pool):
    due = at(17)
    await storage.create_task(pool, text="zz file the report", due_at=due)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO candidate_items (item_type, text, effect, valid_from, valid_until, confidence) "
                              "VALUES ('constraint', 'zz travelling tomorrow', 'tennis will not happen', %s, %s, 0.9)", (DAY, DAY))
            await cur.execute("INSERT INTO candidate_items (item_type, text, valid_from, valid_until, confidence) "
                              "VALUES ('plan', 'zz weak guess', %s, %s, 0.2)", (DAY, DAY))
            await cur.execute("INSERT INTO candidate_items (item_type, text, valid_from, valid_until, confidence) "
                              "VALUES ('plan', 'zz expired fact', %s, %s, 0.9)", (DAY - timedelta(days=5), DAY - timedelta(days=3)))
    env = make_env(monkeypatch, pool)
    await env.generate()
    user = env.prompt()[1]
    assert "zz file the report (due Tue 04 Mar 17:00)" in user
    assert "zz travelling tomorrow -> tennis will not happen" in user
    assert "zz weak guess" not in user and "zz expired fact" not in user
    assert "07:00-23:00" in user                                  # the whole day is free


# ---------- first draft vs the final schedule ----------

async def test_compare_reports_how_the_day_ended_up_against_the_first_draft(monkeypatch, pool):
    env = make_env(monkeypatch, pool)
    draft = await env.draft_via_llm()
    await env.post(f"/schedule/draft/{draft['id']}/accept")
    gym = next(r for r in await env.schedule_rows() if r["title"] == "zz Gym")
    await env.http.patch(f"/schedule/{gym['id']}", json={"title": "zz Gym", "start_at": at(8).isoformat(), "end_at": at(9).isoformat()})
    tennis = next(r for r in await env.schedule_rows() if r["title"] == "zz Tennis")
    await env.http.delete(f"/schedule/{tennis['id']}")
    await storage.create_schedule_entry(pool, title="zz Dentist", start_at=at(15), end_at=at(16))

    out = (await env.get("/schedule/drafts/2031-03-04/compare"))["data"]
    c = out["comparison"]
    assert (out["first_version"], out["final_source"]) == (1, "live")
    assert [m["title"] for m in c["kept"]] == ["zz Deep work"]
    assert [m["title"] for m in c["moved"]] == ["zz Gym"]
    assert [m["title"] for m in c["dropped"]] == ["zz Tennis"]
    assert c["added"] == [{"title": "zz Dentist", "origin": "user"}]
    assert (await env.get("/schedule/drafts/2031-04-01/compare"))["status"] == "error"        # no draft that day


async def test_task_blocks_carry_the_task_id_into_the_schedule_and_the_prompt(monkeypatch, pool):
    task = await storage.create_task(pool, text="zz write report", due_at=at(10, day=DAY) - timedelta(days=1))
    script = json.dumps({"entries": [
        {"title": "zz Write report", "start": "09:00", "end": "11:00", "reason": "overdue", "task_id": task["id"]},
        {"title": "zz Other", "start": "12:00", "end": "12:30", "reason": "x", "task_id": 987654}]})
    env = make_env(monkeypatch, pool, script)
    draft = await env.generate()
    user = env.prompt()[1]
    assert f"[task {task['id']}] zz write report" in user and "overdue by 1 day" in user

    assert [e["task_id"] for e in draft["entries"]] == [task["id"], None]      # an id the model invented is unlinked

    await env.post(f"/schedule/draft/{draft['id']}/accept", {"indexes": [0, 1]})
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT title, task_id FROM schedule WHERE title LIKE 'zz%' ORDER BY start_at")
            assert await cur.fetchall() == [("zz Write report", task["id"]), ("zz Other", None)]
