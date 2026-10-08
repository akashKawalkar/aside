"""Routes for the persistent-file editor and the `wrong:` trigger, with storage replaced by fakes."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import capture.persistent_routes as routes
from capture.router import route
from capture.server import op_error, op_ok
from config import Memory

TS = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def row(id, section, text, status="active"):
    return {"id": id, "section": section, "text": text, "source": "user", "created": TS, "last_confirmed": None,
            "evidence_count": 1, "status": status, "retired_reason": None, "replaces_id": None, "seen_days": []}


@pytest.fixture
def client(monkeypatch):
    calls = []

    async def listing(pool):
        return [row(1, "identity", "Lives in Pune"), row(2, "goals", "Learn tabla"), row(3, "identity", "Old", "retired")]

    async def add(pool, **kw):
        calls.append(("add", kw))
        if kw["text"] == "dup":
            raise ValueError("The entry to replace must be a live entry in the same section.")
        return {"action": "added", "entry": {"id": 9}, "also_changed": [], "evicted": []}

    async def undo(pool, diff_id):
        return None if diff_id == 404 else {"undone": diff_id, "restored": []}

    async def mark(pool, **kw):
        calls.append(("mark", kw))
        return 5

    monkeypatch.setattr(routes, "list_persistent_entries", listing)
    monkeypatch.setattr(routes, "add_persistent_entry", add)
    monkeypatch.setattr(routes, "undo_persistent_change", undo)
    monkeypatch.setattr(routes, "insert_mark_wrong", mark)
    app = FastAPI()
    app.state.db_pool = object()
    app.include_router(routes.make_persistent_router(op_ok, op_error, Memory(cap_tokens=1234, settle_days=3)))
    c = TestClient(app)
    c.calls = calls
    return c


def test_get_groups_entries_by_section_and_reports_live_tokens_and_cap(client):
    data = client.get("/persistent-file").json()["data"]
    assert list(data["sections"]) == ["identity", "preferences", "routine", "goals", "people"]
    assert [e["id"] for e in data["sections"]["identity"]["entries"]] == [1, 3]
    assert data["sections"]["identity"]["always_on"] is True and data["sections"]["goals"]["always_on"] is False
    assert data["cap_tokens"] == 1234 and data["settle_days"] == 3
    assert data["tokens"] == 4 + 4   # "Lives in Pune" and "Learn tabla" at 13 and 11 chars; the retired one is not counted


def test_add_passes_the_memory_config_and_turns_a_refusal_into_an_error_envelope(client):
    ok = client.post("/persistent-file/entries", json={"section": "identity", "text": "Likes tea"}).json()
    assert ok["status"] == "ok" and ok["message"] == "Saved."
    assert client.calls[0][1]["memory"].cap_tokens == 1234
    bad = client.post("/persistent-file/entries", json={"section": "identity", "text": "dup", "replaces_id": 1}).json()
    assert bad["status"] == "error" and "same section" in bad["message"]


def test_undo_unknown_change_is_an_error_envelope(client):
    assert client.post("/persistent-file/undo/404").json()["status"] == "error"
    assert client.post("/persistent-file/undo/3").json()["status"] == "ok"


def test_mark_wrong_logs_the_target_and_optional_reason(client):
    assert client.post("/mark-wrong", json={"target": "diff_log:12", "reason": "not true"}).json()["data"] == {"id": 5}
    assert client.calls[-1] == ("mark", {"target": "diff_log:12", "compile_log_id": None, "reason": "not true"})
    assert client.post("/mark-wrong", json={"target": ""}).status_code == 422


def test_wrong_prefix_routes_to_wrong_with_the_reason_as_text_and_may_be_empty():
    r = route("wrong: it's not Tuesday", "chat", "enter")
    assert (r.destination, r.text, r.error) == ("wrong", "it's not Tuesday", None)
    assert route("WRONG:", "note", "enter").destination == "wrong"
    assert route("\\wrong: a note", "note", "enter").destination == "note"   # the backslash escape still works
