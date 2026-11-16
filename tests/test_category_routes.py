"""tests/test_category_routes.py — unit tests for category rules API."""
from __future__ import annotations

import asyncio
import selectors
from types import SimpleNamespace
import pytest

from capture.category_routes import make_category_router, RuleCreate, RuleUpdate


def op_ok(message: str = "", data: object = None):
    return {"status": "ok", "message": message, "data": data}


def op_error(message: str, data: object = None, *, detail: str | None = None):
    return {"status": "error", "message": message, "detail": detail, "data": data}


def run_async(coro):
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


@pytest.fixture
def endpoints(monkeypatch):
    fake_rules = [
        {
            "id": 1,
            "priority": 50,
            "source": "laptop",
            "app": "Code.exe",
            "domain": None,
            "title_has": [],
            "category": "coding",
            "label": "VS Code",
            "use_title": True,
            "origin": "seed",
            "reviewed": True,
            "enabled": True,
            "note": "",
            "created_at": None,
        },
        {
            "id": 2,
            "priority": 40,
            "source": "laptop",
            "app": "obsidian.exe",
            "domain": None,
            "title_has": [],
            "category": "work_admin",
            "label": "Obsidian",
            "use_title": True,
            "origin": "llm",
            "reviewed": False,
            "enabled": True,
            "note": "suggested by digest",
            "created_at": None,
        },
    ]

    import capture.category_routes as routes

    async def mock_list(pool, *, review_only=False):
        if review_only:
            return [r for r in fake_rules if r["origin"] == "llm" and not r["reviewed"]]
        return fake_rules

    async def mock_create(pool, **kw):
        return 99

    async def mock_approve(pool, rule_id):
        return rule_id == 2

    async def mock_update(pool, rule_id, **kw):
        return rule_id in (1, 2)

    async def mock_delete(pool, rule_id):
        return rule_id in (1, 2)

    monkeypatch.setattr(routes.storage, "list_category_rules", mock_list)
    monkeypatch.setattr(routes.storage, "create_category_rule", mock_create)
    monkeypatch.setattr(routes.storage, "approve_category_rule", mock_approve)
    monkeypatch.setattr(routes.storage, "update_category_rule", mock_update)
    monkeypatch.setattr(routes.storage, "delete_category_rule", mock_delete)

    router = make_category_router(op_ok, op_error)
    return {r.name: r.endpoint for r in router.routes}


@pytest.fixture
def req():
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=None)))


def test_list_rules(endpoints, req):
    res = run_async(endpoints["list_rules"](req))
    assert res["status"] == "ok"
    assert len(res["data"]["rules"]) == 2
    assert "categories" in res["data"]


def test_list_review_only(endpoints, req):
    res = run_async(endpoints["list_rules"](req, review=True))
    assert res["status"] == "ok"
    assert len(res["data"]["rules"]) == 1
    assert res["data"]["rules"][0]["id"] == 2


def test_create_rule(endpoints, req):
    body = RuleCreate(category="study", label="Anki", app="anki.exe")
    res = run_async(endpoints["create_rule"](body, req))
    assert res["status"] == "ok"
    assert res["data"]["id"] == 99


def test_create_rule_invalid_category(endpoints, req):
    body = RuleCreate(category="not_a_category", label="Bad")
    res = run_async(endpoints["create_rule"](body, req))
    assert res["status"] == "error"


def test_approve_rule(endpoints, req):
    res = run_async(endpoints["approve_rule"](2, req))
    assert res["status"] == "ok"


def test_approve_nonexistent_rule(endpoints, req):
    res = run_async(endpoints["approve_rule"](999, req))
    assert res["status"] == "error"


def test_update_rule(endpoints, req):
    body = RuleUpdate(category="study")
    res = run_async(endpoints["update_rule"](2, body, req))
    assert res["status"] == "ok"


def test_update_rule_invalid_category(endpoints, req):
    body = RuleUpdate(category="fake_cat")
    res = run_async(endpoints["update_rule"](2, body, req))
    assert res["status"] == "error"


def test_delete_rule(endpoints, req):
    res = run_async(endpoints["delete_rule"](2, req))
    assert res["status"] == "ok"


def test_delete_nonexistent_rule(endpoints, req):
    res = run_async(endpoints["delete_rule"](999, req))
    assert res["status"] == "error"
