"""Routes for skills, statements / candidate items / instruction records, and notes (edit, delete, `journal:`),
against the real database. Every row these tests create starts with `zz` and only those are removed afterwards.
Skipped if the database is unreachable."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import storage
from capture.server import create_app


@pytest.fixture
async def api():
    pool = storage.make_pool()
    try:
        await pool.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    app = create_app()
    app.state.db_pool = pool                         # ASGITransport does not run the lifespan, so wire the pool by hand
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for table, column in (("skills", "name"), ("statements", "text"), ("candidate_items", "text"),
                                  ("instruction_records", "text"), ("notes", "text")):
                await cur.execute(f"DELETE FROM {table} WHERE {column} LIKE 'zz%'")
    await pool.close()


async def sql(pool, query, *args):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query, args)
            return (await cur.fetchone())[0]


# ---------- skills ----------

async def test_skills_crud_over_http_uses_use_when_and_affects(api):
    client, _ = api
    made = (await client.post("/skills", json={"name": "zz-recap", "body": "Three bullets.", "use_when": "recap, summary", "affects": "reply format"})).json()
    assert made["status"] == "ok"
    skill = made["data"]["skill"]
    assert (skill["use_when"], skill["affects"], skill["usage_count"], skill["correction_count"]) == ("recap, summary", "reply format", 0, 0)

    listed = (await client.get("/skills")).json()["data"]["skills"]
    assert skill["id"] in [s["id"] for s in listed]

    patched = (await client.patch(f"/skills/{skill['id']}", json={"body": "Five bullets.", "correction_count": 1})).json()["data"]["skill"]
    assert (patched["body"], patched["correction_count"], patched["use_when"]) == ("Five bullets.", 1, "recap, summary")

    assert (await client.delete(f"/skills/{skill['id']}")).json()["status"] == "ok"
    assert (await client.delete(f"/skills/{skill['id']}")).json()["status"] == "error"
    assert (await client.patch(f"/skills/{skill['id']}", json={"body": "x"})).json()["status"] == "error"


async def test_skill_validation_rejects_empty_and_oversized_fields(api):
    client, _ = api
    assert (await client.post("/skills", json={"name": "", "body": "x"})).status_code == 422
    assert (await client.post("/skills", json={"name": "zz-x", "body": ""})).status_code == 422
    assert (await client.post("/skills", json={"name": "zz-x", "body": "x", "affects": "a" * 201})).status_code == 422
    assert (await client.post("/skills", json={"name": "z" * 101, "body": "x"})).status_code == 422


# ---------- statements, candidate items, instruction records ----------

@pytest.mark.parametrize("path,table,insert", [
    ("statements", "statements", "INSERT INTO statements (text, source) VALUES ('zz stmt', 'journal') RETURNING id"),
    ("candidate_items", "candidate_items",
     "INSERT INTO candidate_items (item_type, text, confidence) VALUES ('plan', 'zz cand', 0.7) RETURNING id"),
    ("instruction_records", "instruction_records", "INSERT INTO instruction_records (text) VALUES ('zz instr') RETURNING id"),
])
async def test_viewer_lists_and_deletes_each_record_type(api, path, table, insert):
    client, pool = api
    row_id = await sql(pool, insert)

    items = (await client.get(f"/{path}")).json()["data"]["items"]
    assert row_id in [i["id"] for i in items]

    assert (await client.delete(f"/{path}/{row_id}")).json()["status"] == "ok"
    assert await sql(pool, f"SELECT count(*) FROM {table} WHERE id = %s", row_id) == 0
    assert (await client.delete(f"/{path}/{row_id}")).json()["status"] == "error"      # already gone


# ---------- notes ----------

async def test_note_capture_edit_and_delete(api):
    client, pool = api
    saved = (await client.post("/input", json={"text": "note: zz first draft", "mode": "chat", "key": "enter"})).json()["data"]
    note_id = saved["id"]

    edited = (await client.patch(f"/notes/{note_id}", json={"text": "zz second draft", "tags": ["aside"]})).json()
    assert edited["status"] == "ok" and edited["data"]["note"]["text"] == "zz second draft"

    assert (await client.patch(f"/notes/{note_id}", json={"text": ""})).status_code == 422
    assert (await client.patch(f"/notes/{note_id}", json={"text": "x", "surprise": 1})).status_code == 422   # extra=forbid

    assert (await client.delete(f"/notes/{note_id}")).json()["status"] == "ok"
    assert (await client.delete(f"/notes/{note_id}")).json()["status"] == "error"
    assert (await client.patch(f"/notes/{note_id}", json={"text": "zz gone"})).json()["status"] == "error"


async def test_journal_prefix_saves_a_note_tagged_journal(api):
    client, pool = api
    saved = (await client.post("/input", json={"text": "journal: zz felt scattered today", "mode": "chat", "key": "enter"})).json()["data"]
    assert saved["destination"] in ("note", "journal_note") or saved.get("saved_as")
    tags = await sql(pool, "SELECT tags FROM notes WHERE id = %s", saved["id"])
    assert "journal" in tags
    text = await sql(pool, "SELECT text FROM notes WHERE id = %s", saved["id"])
    assert text == "zz felt scattered today"                 # the prefix is routing, not content


async def test_keyword_search_treats_percent_and_underscore_literally(api):
    client, pool = api
    await client.post("/input", json={"text": "note: zz 100% sure", "mode": "chat", "key": "enter"})
    await client.post("/input", json={"text": "note: zz plain text", "mode": "chat", "key": "enter"})
    hits = (await client.get("/notes/search", params={"q": "zz%"})).json()["data"]
    keyword = [h["text"] for h in hits.get("results", hits.get("notes", [])) if h["match"] == "keyword"]
    assert "zz plain text" not in keyword                     # `%` is not a wildcard (a similar note may still match by meaning)


# ---------- keyword search used as chat context ----------

async def test_note_keyword_search_all_words_vs_any_word(api):
    from storage.repo.notes import search_notes
    _, pool = api
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for text in ("zz tennis with Rahul on Friday", "zz Rahul likes chai", "zz unrelated grocery list"):
                await cur.execute("INSERT INTO notes (text, tags, source) VALUES (%s, '[]'::jsonb, 'test')", (text,))
    query = "zz draft an email to Rahul about tennis"
    assert await search_notes(pool, query) == []          # the search box: every word must appear
    hits = await search_notes(pool, query, match_any=True)       # context: any word, best match first
    texts = [h["text"] for h in hits]       # the user's real notes share the table, so check order only between our own
    assert texts.index("zz tennis with Rahul on Friday") < texts.index("zz Rahul likes chai")
    assert "zz unrelated grocery list" not in texts
    assert await search_notes(pool, "to an", match_any=True) == []      # words under 3 letters are ignored
    assert await search_notes(pool, "100%_") == []                      # LIKE wildcards are escaped


async def test_privacy_route_reports_the_rules_read_only(api):
    client, _ = api
    data = (await client.get("/privacy")).json()["data"]
    assert "journal" in data["deny_tags"] and data["background_enabled"] is False
    assert data["calls_today"] >= 0 and data["daily_call_cap"] > 0


# ---------- find: looks notes up locally ----------

async def test_find_trigger_returns_notes_without_saving_or_calling_a_model(api):
    client, pool = api
    await client.post("/input", json={"text": "note: zz tennis with Rahul on Friday", "mode": "chat", "key": "enter"})
    before = await sql(pool, "SELECT count(*) FROM notes")

    reply = (await client.post("/input", json={"text": "find: zz tennis", "mode": "chat", "key": "enter"})).json()
    assert reply["status"] == "ok" and reply["data"]["destination"] == "find"
    assert "zz tennis with Rahul on Friday" in [n["text"] for n in reply["data"]["notes"]]
    assert {"id", "text", "tags", "created_at"} <= set(reply["data"]["notes"][0])

    assert await sql(pool, "SELECT count(*) FROM notes") == before          # nothing saved (not even the query)
    assert "reply" not in reply["data"]                                      # and no model was asked


async def test_find_works_in_any_mode_and_a_natural_question_still_finds_the_note(api):
    client, _ = api
    await client.post("/input", json={"text": "note: zz tennis with Rahul on Friday", "mode": "chat", "key": "enter"})
    reply = (await client.post("/input", json={"text": "find: what did I note about tennis", "mode": "task", "key": "enter"})).json()
    assert reply["data"]["destination"] == "find"
    assert "zz tennis with Rahul on Friday" in [n["text"] for n in reply["data"]["notes"]]


async def test_find_needs_something_to_look_for(api):
    client, _ = api
    reply = (await client.post("/input", json={"text": "find:   ", "mode": "chat", "key": "enter"})).json()
    assert reply["status"] == "error"


async def test_notes_search_route_uses_the_same_lookup(api):
    client, _ = api
    await client.post("/input", json={"text": "note: zz tennis with Rahul on Friday", "mode": "chat", "key": "enter"})
    got = (await client.get("/notes/search", params={"q": "what about tennis"})).json()["data"]["results"]
    assert "zz tennis with Rahul on Friday" in [n["text"] for n in got]
