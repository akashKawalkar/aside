"""Against the real database: observations (repo + routes), restoring a completed task, statements written by
capture, and the extractor worker through the gate. Rows start with `zz` or are dated 2001 and are removed afterwards."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

import storage
from storage.repo.statements import list_statements
from capture.server import create_app
from llm.approved import ApprovalRequired
from llm.client import Completion
from llm.fake import FakeClient


@pytest.fixture
async def api():
    pool = storage.make_pool()
    try:
        await pool.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    app = create_app()
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM observations WHERE observation_key LIKE 'zz:%'")
            await cur.execute("DELETE FROM task_log WHERE snapshot->>'text' LIKE 'zz%'")
            await cur.execute("DELETE FROM tasks WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM statements WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM notes WHERE text LIKE 'zz%'")
            await cur.execute("DELETE FROM candidate_items WHERE text LIKE 'zz%'")
    await pool.close()


def obs(key="zz:one", **over):
    row = {"observation_key": key, "kind": "focus_hour", "text": "zz Activity around 09:00", "status": "active",
           "occurrences": 3, "misses": 0, "first_seen": date(2001, 1, 1), "last_seen": date(2001, 1, 3),
           "last_evaluated_day": date(2001, 1, 3), "evidence": {"hour": 9}}
    return {**row, **over}


# ---------- observations ----------

async def test_observation_upsert_updates_in_place_and_lists(api):
    _, pool = api
    await storage.upsert_observation(pool, obs())
    await storage.upsert_observation(pool, obs(occurrences=4, status="questioned", misses=2))
    rows = [r for r in await storage.list_observations(pool) if r["observation_key"] == "zz:one"]
    assert len(rows) == 1 and (rows[0]["occurrences"], rows[0]["status"], rows[0]["evidence"]) == (4, "questioned", {"hour": 9})


async def test_dropped_observations_can_be_hidden_from_the_list(api):
    _, pool = api
    await storage.upsert_observation(pool, obs("zz:dropped", status="dropped", misses=3))
    keys = lambda rows: [r["observation_key"] for r in rows]
    assert "zz:dropped" in keys(await storage.list_observations(pool))
    assert "zz:dropped" not in keys(await storage.list_observations(pool, include_dropped=False))


async def test_deleting_an_observation_erases_it_and_it_stays_deleted(api):
    client, pool = api
    await storage.upsert_observation(pool, obs("zz:gone"))
    row_id = next(r["id"] for r in await storage.list_observations(pool) if r["observation_key"] == "zz:gone")

    first = (await client.delete(f"/observations/{row_id}")).json()
    assert first["status"] == "ok"
    assert (await client.delete(f"/observations/{row_id}")).json()["status"] == "error"      # already gone

    await storage.upsert_observation(pool, obs("zz:gone", occurrences=9))                     # the next pass must not resurrect it
    assert "zz:gone" not in [r["observation_key"] for r in (await client.get("/observations")).json()["data"]["observations"]]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT text, occurrences, evidence FROM observations WHERE id = %s", (row_id,))
            assert await cur.fetchone() == ("", 0, {})        # the content itself is wiped, not just hidden


# ---------- restoring a completed task ----------

async def test_restore_brings_back_a_completed_task_and_logs_it(api):
    client, pool = api
    made = (await client.post("/tasks", json={"text": "zz restore me"})).json()["data"]
    await client.post(f"/tasks/{made['id']}/complete")
    restored = (await client.post(f"/tasks/{made['id']}/restore")).json()
    assert restored["status"] == "ok" and restored["data"]["task"]["status"] == "pending"
    assert restored["data"]["task"]["completed_at"] is None
    assert [e["action"] for e in await storage.list_task_log(pool, limit=5) if e["task_id"] == made["id"]][:2] == ["restored", "completed"]
    assert (await client.post(f"/tasks/{made['id']}/restore")).json()["status"] == "error"      # not completed any more


async def test_restore_resets_a_passed_due_date_without_logging_a_slip(api):
    client, pool = api
    made = (await client.post("/tasks", json={"text": "zz overdue then done", "due_at": "2001-01-01T10:00:00+05:30"})).json()["data"]
    await client.post(f"/tasks/{made['id']}/complete")
    task = (await client.post(f"/tasks/{made['id']}/restore")).json()["data"]["task"]
    assert datetime.fromisoformat(task["due_at"]) > datetime.now(timezone.utc)      # the expiry worker would delete it otherwise
    since = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert [c for c in await storage.list_task_due_changes(pool, since=since) if c["task_id"] == made["id"]] == []


# ---------- statements written by capture ----------

async def test_notes_and_journal_entries_become_statements(api):
    client, pool = api
    await client.post("/input", json={"text": "note: zz plain thought", "mode": "chat", "key": "enter"})
    await client.post("/input", json={"text": "journal: zz diary thought", "mode": "chat", "key": "enter"})
    got = {s["text"]: s["source"] for s in await list_statements(pool) if s["text"].startswith("zz")}
    assert got == {"zz plain thought": "note", "zz diary thought": "journal"}


# ---------- the extractor worker ----------

async def test_extractor_worker_runs_in_a_background_grant_and_applies_privacy(api, monkeypatch):
    import extractor.worker as worker
    _, pool = api
    for text, source in (("zz run tomorrow", "note"), ("zz secret diary", "journal")):
        await storage.insert_statement(pool, text, source)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE statements SET created_at = '2001-01-04 12:00:00+05:30' WHERE text LIKE 'zz%'")

    fake = FakeClient([Completion(json.dumps({"items": [{"type": "plan", "text": "zz Run tomorrow"}]}), "fake")])
    real_create = worker.create_client

    def gated_fake(**kw):                 # the real factory and gate, but never a network client or a trace row
        return real_create(inner=fake, **{**kw, "save_span": None})

    monkeypatch.setattr(worker, "create_client", gated_fake)
    from config import Privacy, load_config
    real_config = load_config()
    real_config.privacy = Privacy(deny_tags=["journal"])          # the rule is tested explicitly: the shipped default may change
    monkeypatch.setattr("extractor.job.load_config", lambda: real_config)
    now = datetime(2001, 1, 5, 3, 0, tzinfo=timezone.utc)
    assert await worker.run_extractor_once(pool, True, now=now) == 1
    sent = fake.calls[0].messages[1].content
    assert "zz run tomorrow" in sent and "zz secret diary" not in sent       # the journal never leaves
    assert await worker.run_extractor_once(pool, False, now=now) == 0         # off means off
    assert len(fake.calls) == 1


async def test_a_background_call_without_a_grant_is_refused():
    from llm.factory import create_client

    async def zero():
        return 0

    client = create_client(inner=FakeClient(), count_today=zero, daily_call_cap=5, background_enabled=True)
    with pytest.raises(ApprovalRequired):
        await client.complete([], max_tokens=10)
