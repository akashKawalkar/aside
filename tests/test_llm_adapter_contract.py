"""Contract every LLMClient adapter must meet, run against a mocked HTTP layer (no network).
To add a provider: write llm/adapters/<name>.py, then add one AdapterCase to CASES below that knows how to build the
client around a fake `post` and how that provider's wire format looks. Everything else here then applies to it."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from llm.adapters.openai_compatible import HttpResponse, OpenAICompatibleClient
from llm.client import LLMClient, Message, ModelProfile, ToolCall
from llm.errors import (
    LLMAuthError, LLMBadRequest, LLMError, LLMRateLimited, LLMResponseError, LLMServerError, LLMTimeout, LLMUnavailable,
)

PROFILE = ModelProfile(name="m-1", provider="test", window=8000, supports_tools=True, supports_json=True)
NO_TOOLS = ModelProfile(name="m-0", provider="test", window=8000, supports_tools=False, supports_json=False)
SECRET = "sk-test-SECRET-1234"
HELLO = [Message("system", "be brief"), Message("user", "hi")]


@dataclass
class AdapterCase:
    name: str
    make: Callable[..., LLMClient]                       # (post, profile=PROFILE) -> client
    text: Callable[..., HttpResponse]                    # (text, ...) -> a successful text reply
    tool_call: Callable[..., HttpResponse]               # (id, name, arguments) -> a reply asking for a tool
    status: Callable[..., HttpResponse]                  # (status, message, headers) -> an error reply
    broken: Callable[[], HttpResponse]                   # a 200 that is not a usable reply
    # What the adapter sent, extracted from the recorded (url, headers, body):
    url_ok: Callable[[str], bool]
    sent_roles: Callable[[dict], list[str]]
    sent_max_tokens: Callable[[dict], int]
    sent_tool_names: Callable[[dict], list[str]]
    auth_header: Callable[[dict], str | None]


def _openai_case() -> AdapterCase:
    def text(text="hello", *, model="m-1-2026", finish="stop", prompt=12, completion=3):
        return HttpResponse(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}],
                                  "usage": {"prompt_tokens": prompt, "completion_tokens": completion}})

    def tool_call(id="call_1", name="add_task", arguments=None):
        raw = json.dumps(arguments or {})
        return HttpResponse(200, {"model": "m-1", "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": id, "type": "function", "function": {"name": name, "arguments": raw}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8}})

    return AdapterCase(
        name="openai-compatible",
        make=lambda post, profile=PROFILE: OpenAICompatibleClient(profile, base_url="https://api.example.test/v1/", api_key=SECRET, post=post),
        text=text,
        tool_call=tool_call,
        status=lambda status, message="nope", headers=None: HttpResponse(status, {"error": {"message": message}}, headers or {}),
        broken=lambda: HttpResponse(200, {"unexpected": True}),
        url_ok=lambda url: url == "https://api.example.test/v1/chat/completions",
        sent_roles=lambda body: [m["role"] for m in body["messages"]],
        sent_max_tokens=lambda body: body["max_tokens"],
        sent_tool_names=lambda body: [t["function"]["name"] for t in body.get("tools", [])],
        auth_header=lambda headers: headers.get("Authorization"),
    )


CASES = [_openai_case()]


class FakePost:
    """Stands in for the HTTP layer: replies with `reply` (or raises it), and records every request."""

    def __init__(self, reply: HttpResponse | BaseException):
        self.reply, self.requests = reply, []

    async def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, body, timeout))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


@pytest.fixture(params=CASES, ids=lambda c: c.name)
def case(request) -> AdapterCase:
    return request.param


async def call(client, **kw):
    return await client.complete(kw.pop("messages", HELLO), max_tokens=kw.pop("max_tokens", 100), **kw)


# ---------- interface ----------

def test_satisfies_the_protocol_and_exposes_its_profile(case):
    client = case.make(FakePost(case.text()))
    assert isinstance(client, LLMClient) and client.profile is PROFILE


# ---------- request ----------

async def test_request_goes_to_the_right_url_with_auth_roles_and_limits(case):
    post = FakePost(case.text())
    await call(case.make(post), max_tokens=77, temperature=0.3)
    [(url, headers, body, timeout)] = post.requests
    assert case.url_ok(url) and case.auth_header(headers) == f"Bearer {SECRET}"
    assert case.sent_roles(body) == ["system", "user"] and case.sent_max_tokens(body) == 77
    assert body["model"] == "m-1" and body["temperature"] == 0.3 and timeout > 0


async def test_tools_are_sent_in_the_providers_format(case):
    post = FakePost(case.text())
    tools = [{"name": "add_task", "description": "Add a task", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}}}]
    await call(case.make(post), tools=tools)
    assert case.sent_tool_names(post.requests[0][2]) == ["add_task"]


async def test_a_tool_conversation_can_be_sent_back(case):
    post = FakePost(case.text("done"))
    history = [Message("user", "add milk"),
               Message("assistant", "", tool_calls=(ToolCall("call_1", "add_task", {"text": "milk"}),)),
               Message("tool", '{"ok": true}', tool_call_id="call_1")]
    await call(case.make(post), messages=history)
    assert case.sent_roles(post.requests[0][2]) == ["user", "assistant", "tool"]


async def test_capabilities_the_model_lacks_are_refused_before_any_request(case):
    post = FakePost(case.text())
    client = case.make(post, NO_TOOLS)
    with pytest.raises(LLMBadRequest):
        await call(client, tools=[{"name": "t", "parameters": {}}])
    with pytest.raises(LLMBadRequest):
        await call(client, response_format={"type": "json_object"})
    assert post.requests == []


# ---------- response ----------

async def test_a_text_reply_is_normalised(case):
    out = await call(case.make(FakePost(case.text("Hi there", model="m-1-2026", prompt=12, completion=3))))
    assert (out.text, out.model, out.finish_reason, out.tool_calls) == ("Hi there", "m-1-2026", "stop", ())
    assert (out.usage.input_tokens, out.usage.output_tokens) == (12, 3) and out.latency_ms >= 0


async def test_a_tool_call_reply_is_normalised(case):
    out = await call(case.make(FakePost(case.tool_call("call_9", "add_task", {"text": "milk", "n": 2}))))
    assert out.text == "" and out.finish_reason == "tool_calls"
    assert out.tool_calls == (ToolCall("call_9", "add_task", {"text": "milk", "n": 2}),)


async def test_a_truncated_reply_says_so(case):
    assert (await call(case.make(FakePost(case.text("cut off", finish="length"))))).finish_reason == "length"


async def test_an_unusable_200_is_a_response_error(case):
    with pytest.raises(LLMResponseError):
        await call(case.make(FakePost(case.broken())))


# ---------- failures ----------

@pytest.mark.parametrize("status, expected", [
    (401, LLMAuthError), (403, LLMAuthError), (400, LLMBadRequest), (404, LLMBadRequest), (422, LLMBadRequest),
    (429, LLMRateLimited), (500, LLMServerError), (503, LLMServerError),
])
async def test_http_errors_map_to_our_error_kinds_and_keep_the_providers_message(case, status, expected):
    with pytest.raises(expected, match="provider said no"):
        await call(case.make(FakePost(case.status(status, "provider said no"))))


async def test_a_rate_limit_carries_the_retry_hint(case):
    with pytest.raises(LLMRateLimited) as hint:
        await call(case.make(FakePost(case.status(429, "slow down", {"retry-after": "7"}))))
    assert hint.value.retry_after == 7.0
    with pytest.raises(LLMRateLimited) as none:
        await call(case.make(FakePost(case.status(429, "slow down"))))
    assert none.value.retry_after is None


@pytest.mark.parametrize("boom, expected", [
    (asyncio.TimeoutError(), LLMTimeout), (TimeoutError(), LLMTimeout),
    (ConnectionRefusedError("refused"), LLMUnavailable), (OSError("dns"), LLMUnavailable),
])
async def test_transport_failures_map_to_timeout_or_unavailable(case, boom, expected):
    with pytest.raises(expected):
        await call(case.make(FakePost(boom)))


async def test_every_failure_is_an_llm_error(case):
    for reply in (case.status(500), case.status(401), case.broken(), TimeoutError()):
        with pytest.raises(LLMError):
            await call(case.make(FakePost(reply)))


async def test_the_api_key_never_leaks_into_errors_or_repr(case):
    client = case.make(FakePost(case.status(401, "bad key")))
    assert SECRET not in repr(client)
    with pytest.raises(LLMAuthError) as e:
        await call(client)
    assert SECRET not in str(e.value)


# ---------- the real transport (a tiny local server, no internet) ----------

async def test_default_transport_round_trips_over_real_http():
    from aiohttp import web

    seen = {}

    async def chat(request):
        seen["auth"], seen["body"] = request.headers.get("Authorization"), await request.json()
        if seen["body"]["messages"][-1]["content"] == "limit":
            return web.json_response({"error": {"message": "slow down"}}, status=429, headers={"Retry-After": "3"})
        return web.json_response({"model": "m-1", "choices": [{"message": {"content": "pong"}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        client = OpenAICompatibleClient(PROFILE, base_url=f"http://127.0.0.1:{port}/v1", api_key=SECRET, timeout=5)
        out = await client.complete([Message("user", "ping")], max_tokens=10)
        assert (out.text, out.usage.input_tokens, out.finish_reason) == ("pong", 5, "stop")
        assert seen["auth"] == f"Bearer {SECRET}" and seen["body"]["max_tokens"] == 10

        with pytest.raises(LLMRateLimited) as hint:
            await client.complete([Message("user", "limit")], max_tokens=10)
        assert hint.value.retry_after == 3.0
    finally:
        await runner.cleanup()

    # Nothing is listening any more. Windows reports a refused connection only after ~2s, so a short timeout
    # may fire first: either is a correct transport failure.
    with pytest.raises((LLMUnavailable, LLMTimeout)):
        await OpenAICompatibleClient(PROFILE, base_url=f"http://127.0.0.1:{port}/v1", timeout=1).complete([Message("user", "x")], max_tokens=1)
