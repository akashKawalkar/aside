from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from context.compile import compile_context
from context.items import Item, Situation
from context.recipe import load_recipe
from context.sources.current_session import CurrentSessionSource
from context.sources.schedule import ScheduleSource
from context.sources.tasks import TasksSource
from context.sources import EmptySource
from llm.client import Completion, LLMClient, Message, ModelProfile, ToolCall, Usage, load_profile
from llm.fake import FakeClient
from llm.replay import replay_compile
from llm.tokens import estimate_messages, estimate_tokens
from llm.trace import record, span_from_completion

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


# ---------- client / profile / tokens ----------

def test_shipped_profiles_load():
    p = load_profile("fake")
    assert p.name == "fake" and p.window > 0 and p.supports_tools
    default = load_profile()                       # whatever the user chose as default must be a complete, priced profile
    assert default.window > 0 and default.provider and default.input_cost >= 0 and default.output_cost >= 0
    if default.provider != "fake":                 # a real model with no price would make the cost estimate say $0
        assert default.input_cost > 0 and default.output_cost > 0


def test_unknown_profile_is_an_error():
    with pytest.raises(ValueError):
        load_profile("nope")


def test_cost_is_per_million_tokens():
    p = ModelProfile("m", "x", 1000, input_cost=3.0, output_cost=15.0)
    assert p.cost(Usage(1_000_000, 100_000)) == pytest.approx(4.5)


def test_estimate_is_conservative_and_monotonic():
    p = ModelProfile("m", "x", 1000, chars_per_token=4.0, safety_margin=1.25)
    assert estimate_tokens("", p) == 0
    assert estimate_tokens("a" * 400, p) == 125          # 400/4 * 1.25
    assert estimate_tokens("a" * 800, p) > estimate_tokens("a" * 400, p)
    assert estimate_messages([Message("user", "a" * 400)], p) == 125 + 4


def test_fake_client_satisfies_the_protocol():
    assert isinstance(FakeClient(), LLMClient)


# ---------- fake client ----------

async def test_fake_client_script_repeats_last_and_records_calls():
    client = FakeClient(["one", "two"])
    msgs = [Message("user", "hi")]
    texts = [(await client.complete(msgs, max_tokens=50, temperature=0.2)).text for _ in range(3)]
    assert texts == ["one", "two", "two"]
    assert len(client.calls) == 3 and client.calls[0].messages == msgs
    assert (client.calls[0].max_tokens, client.calls[0].temperature) == (50, 0.2)


async def test_fake_client_reply_function_tool_call_and_default():
    echo = FakeClient(reply=lambda m: "echo:" + m[-1].content)
    out = await echo.complete([Message("user", "yo")], max_tokens=10)
    assert out.text == "echo:yo" and out.usage.input_tokens > 0 and out.usage.output_tokens > 0

    call = ToolCall("c1", "add_task", {"text": "x"})
    out = await FakeClient([call]).complete([Message("user", "?")], max_tokens=10)
    assert out.tool_calls == (call,) and out.finish_reason == "tool_calls"

    assert (await FakeClient().complete([Message("user", "?")], max_tokens=10)).text == "ok"
    done = Completion("exact", "m")
    assert await FakeClient([done]).complete([Message("user", "?")], max_tokens=10) is done


# ---------- trace ----------

async def test_span_carries_otel_shaped_fields_and_saves():
    client = FakeClient(["fine"])
    completion = await client.complete([Message("user", "hello there")], max_tokens=20)
    parent = span_from_completion(completion, client.profile, operation="chat", compile_log_id=9)
    child = span_from_completion(completion, client.profile, operation="tool", parent=parent)
    assert child.trace_id == parent.trace_id and child.parent_span_id == parent.span_id and child.span_id != parent.span_id
    assert (parent.provider, parent.model, parent.finish_reason, parent.compile_log_id) == ("fake", "fake", "stop", 9)
    assert parent.input_tokens == completion.usage.input_tokens and parent.attrs["cost_usd"] == 0.0

    saved = []

    async def save(row):
        saved.append(row)

    assert await record(parent, save=save) is parent
    assert saved[0]["operation"] == "chat" and saved[0]["trace_id"] == parent.trace_id
    await record(parent)   # no sink: keeps nothing, raises nothing


# ---------- end to end: compile -> call -> trace -> replay under two recipes ----------

class NotesSource:
    name = "notes"

    async def fetch(self, situation):
        return [Item("note:1", "Prefers short answers", "notes", provenance="note:1")]


async def test_compile_call_trace_replay_under_two_recipes():
    profile = load_profile("fake")

    async def fetch_range(*, start, end):
        return [{"id": 1, "title": "Gym", "start_at": NOW + timedelta(hours=2), "end_at": NOW + timedelta(hours=3)}]

    async def fetch_tasks(*, status):
        return [{"id": 5, "text": "Pay rent", "due_at": NOW + timedelta(days=1)}]

    sources = {
        "current_session": CurrentSessionSource(),
        "persistent_file": EmptySource("persistent_file"),
        "skills": EmptySource("skills"),
        "schedule": ScheduleSource(fetch_range, clock=lambda: NOW),
        "tasks": TasksSource(fetch_tasks),
        "notes": NotesSource(),
        "observations": EmptySource("observations"),
        "history": EmptySource("history"),
    }

    logs: list[dict] = []
    spans: list[dict] = []

    async def save_log(log):
        logs.append(log)
        return len(logs)

    async def save_span(row):
        spans.append(row)

    chat = load_recipe("chat")
    compiled = await compile_context(Situation("chat", query="What's on today?"), chat, sources, profile,
                                     save_log=save_log, now=NOW)
    assert compiled.id == 1
    for needle in ("Gym", "Pay rent", "Prefers short answers", "What's on today?"):
        assert needle in compiled.text

    client = FakeClient(["You have Gym at 7:30 pm."])
    completion = await client.complete(
        [Message("system", compiled.text), Message("user", "What's on today?")], max_tokens=200)
    await record(span_from_completion(completion, client.profile, operation="chat", compile_log_id=compiled.id), save=save_span)
    assert client.calls[0].messages[0].content == compiled.text
    assert spans[0]["compile_log_id"] == compiled.id and spans[0]["input_tokens"] > 0

    stored = logs[0]
    extraction = await replay_compile(stored, load_recipe("extraction"), profile)
    schedule = await replay_compile(stored, load_recipe("schedule"), profile)
    same = await replay_compile(stored, chat, profile)

    assert same.text == compiled.text and not same.only_in_original and not same.only_in_replay
    assert extraction.text != compiled.text and schedule.text != compiled.text and extraction.text != schedule.text
    assert "note:1" in extraction.only_in_original and "task:5" in extraction.only_in_original   # extraction has no notes/tasks
    assert "Pay rent" in schedule.text and "Prefers short answers" not in schedule.text           # schedule recipe has no notes
    assert any(d.reason == "not_in_recipe" for d in extraction.result.dropped)
