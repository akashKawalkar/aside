from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import capture.context_routes as routes
from capture.server import op_error, op_ok

TS = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def stored():
    def it(id, source, text):
        return {"id": id, "text": text, "source": source, "priority": 0, "tokens": len(text), "provenance": "",
                "confidence": 1.0, "valid_from": None, "valid_to": None, "status": "active", "core": False,
                "situations": [], "descriptor": ""}
    return {
        "id": 3, "ts": TS, "situation": "chat", "recipe_name": "chat", "recipe_hash": "abc", "model": "fake",
        "offered": [it("task:1", "tasks", "Pay rent"), it("note:1", "notes", "Short answers"), it("note:2", "notes", "Old")],
        "chosen": [{"id": "task:1", "source": "tasks", "tokens": 8}, {"id": "note:1", "source": "notes", "tokens": 13}],
        "dropped": [{"id": "note:2", "source": "notes", "reason": "source_cap", "tokens": 3}],
        "tokens": 21, "query": "hi",
    }


@pytest.fixture
def client(monkeypatch):
    async def get(pool, log_id):
        return stored() if log_id == 3 else None

    async def listing(pool, *, limit):
        return [{"id": 3, "limit": limit}]

    monkeypatch.setattr(routes, "get_compile_log", get)
    monkeypatch.setattr(routes, "list_compile_logs", listing)
    app = FastAPI()
    app.state.db_pool = object()
    app.include_router(routes.make_context_router(op_ok, op_error))
    return TestClient(app)


def test_list_clamps_limit(client):
    assert client.get("/context/compiles?limit=9999").json()["data"]["items"] == [{"id": 3, "limit": 200}]


def test_detail_has_breakdown_and_chosen_text_but_not_the_full_offer(client):
    data = client.get("/context/compiles/3").json()["data"]
    assert "offered" not in data
    assert [c["text"] for c in data["chosen"]] == ["Pay rent", "Short answers"]
    assert data["by_source"] == {
        "tasks": {"offered": 1, "chosen": 1, "dropped": 0, "tokens": 8},
        "notes": {"offered": 2, "chosen": 1, "dropped": 1, "tokens": 13},
    }


def test_missing_compile_is_an_error_envelope(client):
    body = client.get("/context/compiles/99").json()
    assert body["status"] == "error" and "No such compile" in body["message"]


def test_replay_under_another_recipe(client):
    data = client.post("/context/compiles/3/replay?recipe=extraction").json()["data"]
    assert data["recipe"] == "extraction" and data["text"] == ""        # extraction has neither tasks nor notes
    assert sorted(data["only_in_original"]) == ["note:1", "task:1"] and data["only_in_replay"] == []
    assert {d["reason"] for d in data["dropped"]} == {"not_in_recipe"}


def test_replay_unknown_recipe_lists_the_choices(client):
    body = client.post("/context/compiles/3/replay?recipe=nope").json()
    assert body["status"] == "error" and "chat" in body["detail"]
