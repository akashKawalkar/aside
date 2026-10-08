"""The call-path decisions that rest on measurements of the real API (docs/gemini_api_findings.md): hidden thinking tokens,
reasoning effort per model, fallback to another model when one is overloaded, the provider's day boundary, and honest
token estimates. All with fakes: no network, no quota."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from llm.adapters.openai_compatible import HttpResponse, OpenAICompatibleClient
from llm.approved import ApprovalRequired, Grant, QuotaExceeded, approved
from llm.client import Completion, Message, ModelProfile, Usage, load_profile
from llm.errors import LLMAuthError, LLMBadRequest, LLMRateLimited, LLMServerError, LLMTimeout
from llm.factory import create_client
from llm.fake import FakeClient
from llm.quota import provider_day_start
from llm.resilient import FallbackClient
from llm.tokens import estimate_messages
from llm.trace import span_from_completion

MSGS = [Message("user", "hi")]


def ok_body(prompt=9, completion=1, total=114, finish="stop", text="OK"):
    return {"model": "gemini-x", "choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}}


def adapter(body, profile=None, seen=None):
    async def post(url, headers, payload, timeout):
        if seen is not None:
            seen.append(payload)
        return HttpResponse(200, body)
    return OpenAICompatibleClient(profile or ModelProfile("gemini-x", "gemini", 1000), base_url="https://x.test/v1", api_key="k", post=post)


async def _zero():
    return 0


# ---------- hidden thinking tokens ----------

async def test_thinking_tokens_are_the_gap_between_the_total_and_the_visible_parts():
    completion = await adapter(ok_body(prompt=9, completion=1, total=114)).complete(MSGS, max_tokens=300)
    assert (completion.usage.input_tokens, completion.usage.output_tokens, completion.usage.thinking_tokens) == (9, 1, 104)


async def test_no_total_means_no_thinking_is_claimed():
    body = ok_body()
    del body["usage"]["total_tokens"]
    assert (await adapter(body).complete(MSGS, max_tokens=300)).usage.thinking_tokens == 0


async def test_a_reply_cut_off_by_thinking_is_reported_as_length():
    """Measured: max_tokens=50 returned an empty answer, finish_reason=length, because ~46 hidden tokens used the budget."""
    completion = await adapter(ok_body(prompt=9, completion=0, total=55, finish="length", text="")).complete(MSGS, max_tokens=50)
    assert (completion.text, completion.finish_reason, completion.usage.thinking_tokens) == ("", "length", 46)


def test_thinking_is_billed_as_output_and_shows_in_the_trace():
    profile = ModelProfile("m", "gemini", 1000, input_cost=1.0, output_cost=4.0)
    usage = Usage(1000, 100, 900)
    assert profile.cost(usage) == pytest.approx((1000 * 1.0 + 1000 * 4.0) / 1_000_000)
    span = span_from_completion(Completion("x", "m", usage, finish_reason="length"), profile, operation="chat")
    assert span.attrs["thinking_tokens"] == 900 and span.attrs["truncated"] is True and span.attrs["cost_usd"] > 0
    assert span_from_completion(Completion("x", "m", Usage(1, 1)), profile, operation="chat").attrs["truncated"] is False


# ---------- reasoning effort ----------

async def test_the_profile_effort_is_sent_and_absent_when_unset():
    seen: list = []
    await adapter(ok_body(), ModelProfile("g", "gemini", 1000, reasoning_effort="low"), seen).complete(MSGS, max_tokens=10)
    await adapter(ok_body(), ModelProfile("g", "gemini", 1000), seen).complete(MSGS, max_tokens=10)
    assert seen[0]["reasoning_effort"] == "low" and "reasoning_effort" not in seen[1]


def test_every_shipped_gemini_profile_is_sane():
    import tomllib

    from llm.client import MODELS_PATH

    data = tomllib.loads(MODELS_PATH.read_text(encoding="utf-8"))["models"]
    for name, profile in data.items():
        if profile["provider"] != "gemini":
            continue
        assert profile.get("reasoning_effort", "low") in {"minimal", "low", "medium", "high"}, name
        assert profile["chars_per_token"] <= 3.5, f"{name}: 4.0 under-counted a real schedule prompt (measured 3.38)"
    assert load_profile().reasoning_effort in {"minimal", "low"}, "the default model must not run at its default (medium) thinking"
    if "gemini-3.7-flash" in data:                                           # it answers HTTP 400 to "minimal"
        assert data["gemini-3.7-flash"].get("reasoning_effort") != "minimal"


def test_the_configured_fallback_has_a_profile_and_differs_from_the_default():
    from config import load_config

    fallback = load_config().llm.fallback_model
    if fallback:
        assert load_profile(fallback).name == fallback != load_profile().name


def test_our_estimate_is_not_below_the_measured_token_count_for_a_structured_prompt():
    """Measured on 2026-10-07: a schedule-shaped prompt of 1793 chars was 531 real tokens (3.38 chars/token)."""
    text = "\n".join(f"- {h:02d}:00-{h:02d}:30 Some block title, with markdown ### and times 07:00-23:00" for h in range(7, 23))
    assert estimate_messages([Message("user", text)], load_profile()) >= len(text) / 3.38


# ---------- the provider's day ----------

@pytest.mark.parametrize("now_utc,expected_utc", [
    (datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)),    # PDT: midnight = 07:00 UTC = 12:30 IST
    (datetime(2026, 10, 7, 6, 59, tzinfo=timezone.utc), datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)),    # a minute before the reset: still yesterday
    (datetime(2026, 12, 7, 14, 0, tzinfo=timezone.utc), datetime(2026, 12, 7, 8, 0, tzinfo=timezone.utc)),    # PST: midnight = 08:00 UTC = 13:30 IST
])
def test_the_quota_day_starts_at_midnight_pacific(now_utc, expected_utc):
    assert provider_day_start(now_utc).astimezone(timezone.utc) == expected_utc


# ---------- fallback ----------

def failing(error, name="primary"):
    def reply(messages):
        raise error
    return FakeClient(reply=reply, profile=ModelProfile(name, "gemini", 1000))


def answering(text="from fallback", name="backup"):
    return FakeClient([text], profile=ModelProfile(name, "gemini", 1000))


async def run(primary, backup, cap=10):
    spans = []

    async def save(row):
        spans.append(row)

    async def count():
        return len(spans)

    client = create_client(primary.profile, count_today=count, daily_call_cap=cap, save_span=save, inner=primary, fallback_inner=backup)
    with approved(Grant("user", operation="chat")):
        return client, spans, await client.complete(MSGS, max_tokens=10)


@pytest.mark.parametrize("error", [LLMServerError("HTTP 503: high demand"), LLMTimeout("no answer within 45s"), LLMRateLimited("HTTP 429")])
async def test_an_overloaded_or_silent_model_falls_back_and_both_attempts_are_traced(error):
    client, spans, completion = await run(failing(error), answering())
    assert isinstance(client, FallbackClient)
    assert (completion.text, completion.fallback_from) == ("from fallback", "primary")
    assert [(s["model"], bool(s["error"])) for s in spans] == [("primary", True), ("backup", False)]      # each attempt is a gated, traced call


@pytest.mark.parametrize("error", [LLMAuthError("bad key"), LLMBadRequest("HTTP 400: MINIMAL is not supported")])
async def test_errors_a_second_model_would_repeat_are_not_retried(error):
    backup = answering()
    with pytest.raises(type(error)):
        await run(failing(error), backup)
    assert backup.calls == []


async def test_a_healthy_primary_never_touches_the_fallback():
    backup = answering()
    _, spans, completion = await run(answering("fine", name="primary"), backup)
    assert completion.fallback_from is None and backup.calls == [] and len(spans) == 1


async def test_when_both_fail_the_original_error_type_stands_and_names_the_fallback():
    with pytest.raises(LLMServerError, match="fallback backup also failed"):
        await run(failing(LLMServerError("HTTP 503")), failing(LLMServerError("HTTP 500"), name="backup"))


async def test_the_fallback_respects_the_daily_cap():
    """cap=1: the failed first attempt used the only call, so the fallback may not run and the original failure stands."""
    backup = answering()
    with pytest.raises(LLMServerError, match="503"):
        await run(failing(LLMServerError("HTTP 503")), backup, cap=1)
    assert backup.calls == []


async def test_gate_refusals_are_never_swallowed_by_the_fallback():
    primary = answering(name="primary")
    client = create_client(primary.profile, count_today=_zero, daily_call_cap=5, inner=primary, fallback_inner=answering())
    with pytest.raises(ApprovalRequired):                                 # no grant
        await client.complete(MSGS, max_tokens=5)
    capped = create_client(primary.profile, count_today=_zero, daily_call_cap=0, inner=primary, fallback_inner=answering())
    with approved(Grant("user")), pytest.raises(QuotaExceeded):
        await capped.complete(MSGS, max_tokens=5)


def test_a_test_with_an_injected_client_cannot_reach_the_network_through_the_fallback():
    primary = answering(name="primary")
    client = create_client(primary.profile, count_today=_zero, daily_call_cap=5, inner=primary, fallback="gemini-3.1-flash-lite")
    assert not isinstance(client, FallbackClient)                          # a fallback is named, but no fallback_inner: none is built


def test_no_fallback_when_it_is_the_same_model():
    primary = answering(name="same")
    twin = FakeClient(profile=primary.profile)
    assert not isinstance(create_client(primary.profile, count_today=_zero, daily_call_cap=5, inner=primary, fallback_inner=twin), FallbackClient)
