"""Chat goes straight to the model (no approval step): Enter compiles the context, calls the gated client under a user grant
and returns the reply. The daily cap and the trace still apply. FakeClient only; the 2031 clock keeps real days untouched."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import storage
from capture.server import create_app
from llm.client import Completion, ModelProfile, Usage
from llm.errors import LLMError
from llm.fake import FakeClient

PROFILE = ModelProfile(name="fake-chat", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0,
                       supports_tools=True, supports_json=True)


class Failing(FakeClient):
    async def complete(self, *a, **kw):
        raise LLMError("503 high demand")


@pytest.fixture
async def env(monkeypatch):
    import capture.llm_run as run

    pool = storage.make_pool()
    try:
        await pool.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    traces, state = [], {"used": 0}

    async def save_trace(p, row): traces.append(row)
    async def count(p): return state["used"] + len(traces)
    async def save_compile(p, row): return None
    monkeypatch.setattr(run, "insert_llm_trace", save_trace)
    monkeypatch.setattr(run, "calls_today", count)
    monkeypatch.setattr(storage, "insert_compile_log", save_compile)

    def build(client):
        app = create_app()
        app.state.db_pool, app.state.llm_profile, app.state.llm_client = pool, PROFILE, client
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    yield build, traces, state
    await pool.close()


async def ask(http, text="zz what is two plus two"):
    return (await http.post("/input", json={"text": text, "mode": "chat", "key": "enter"})).json()


async def test_enter_in_chat_returns_the_reply_at_once_and_leaves_a_trace(env):
    build, traces, _ = env
    fake = FakeClient(["four"], profile=PROFILE)
    out = await ask(build(fake))

    assert out["status"] == "ok" and out["data"]["destination"] == "chat_llm"
    assert out["data"]["reply"] == "four" and out["data"]["model"] == "fake-chat" and not out["data"].get("approval_required")
    assert len(fake.calls) == 1 and [m.role for m in fake.calls[0].messages] == ["system", "user"]
    (trace,) = traces
    assert trace["operation"] == "chat" and trace["error"] is None


async def test_a_spent_cap_refuses_without_calling_the_model(env):
    from config import load_config
    build, traces, state = env
    state["used"] = load_config().llm.daily_call_cap
    fake = FakeClient(["never"], profile=PROFILE)
    out = await ask(build(fake))

    assert out["status"] == "error" and "cap" in out["message"]
    assert fake.calls == [] and traces == []


async def test_a_failed_call_is_reported_and_counted(env):
    build, traces, _ = env
    out = await ask(build(Failing(profile=PROFILE)))

    assert out["status"] == "error" and "503" in out["message"]
    assert len(traces) == 1 and "503" in traces[0]["error"]


async def test_a_cut_off_reply_says_so(env):
    build, _, _ = env
    cut = Completion("half an ans", "fake-chat", Usage(10, 5), 0.0, "length")
    out = await ask(build(FakeClient([cut], profile=PROFILE)))
    assert out["data"]["finish_reason"] == "length"
