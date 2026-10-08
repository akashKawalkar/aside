"""The approval/quota chokepoint (llm/approved.py): no grant -> refused, over the cap -> refused without a call, and
every call that does go out is traced, failures included (they use the provider's allowance too)."""
from __future__ import annotations

import pytest

from llm.approved import ApprovalRequired, ApprovedClient, Grant, QuotaExceeded, approved
from llm.client import Message
from llm.errors import LLMError, LLMRateLimited
from llm.factory import create_client
from llm.fake import FakeClient

MSGS = [Message("user", "hi")]


class Harness:
    """An ApprovedClient whose 'today' count is the number of spans it has saved, like the real llm_trace count."""

    def __init__(self, *, cap=3, background=False, inner=None, save_raises=False):
        self.spans: list[dict] = []
        self.inner = inner or FakeClient(["hello"])

        async def save(row):
            if save_raises:
                raise RuntimeError("trace table down")
            self.spans.append(row)

        async def count():
            return len(self.spans)

        self.client = ApprovedClient(self.inner, count_today=count, daily_call_cap=cap, background_enabled=background, save_span=save)

    async def call(self, grant=Grant("user", compile_log_id=7)):
        with approved(grant):
            return await self.client.complete(MSGS, max_tokens=50, temperature=0.2)


async def test_a_call_with_no_grant_is_refused_and_never_reaches_the_model():
    h = Harness()
    with pytest.raises(ApprovalRequired):
        await h.client.complete(MSGS, max_tokens=50)
    assert h.inner.calls == [] and h.spans == []


async def test_the_grant_ends_with_its_block():
    h = Harness()
    assert (await h.call()).text == "hello"
    with pytest.raises(ApprovalRequired):                       # same client, outside the `with`: refused again
        await h.client.complete(MSGS, max_tokens=50)


async def test_background_calls_need_the_flag_and_user_calls_do_not():
    off = Harness(background=False)
    with pytest.raises(ApprovalRequired, match="background"):
        await off.call(Grant("background", operation="extraction"))
    assert off.inner.calls == []
    assert (await off.call(Grant("user"))).text == "hello"

    on = Harness(background=True)
    assert (await on.call(Grant("background", operation="extraction"))).text == "hello"
    assert on.spans[0]["operation"] == "extraction" and on.spans[0]["attrs"]["tier"] == "background"


async def test_success_is_traced_with_its_compile_and_request_settings():
    h = Harness()
    await h.call(Grant("user", operation="chat", compile_log_id=7))
    (span,) = h.spans
    assert (span["compile_log_id"], span["operation"], span["error"]) == (7, "chat", None)
    assert span["attrs"]["max_tokens"] == 50 and span["attrs"]["temperature"] == 0.2
    assert span["input_tokens"] > 0 and "cost_usd" in span["attrs"]


async def test_the_cap_refuses_the_call_that_would_exceed_it_and_makes_no_call():
    h = Harness(cap=2)
    await h.call()
    await h.call()
    with pytest.raises(QuotaExceeded) as e:
        await h.call()
    assert (e.value.used, e.value.cap) == (2, 2) and "daily_call_cap" in str(e.value)
    assert len(h.inner.calls) == 2 and len(h.spans) == 2        # the refused call neither ran nor was traced


async def test_a_cap_of_zero_blocks_everything():
    h = Harness(cap=0)
    with pytest.raises(QuotaExceeded):
        await h.call()
    assert h.inner.calls == []


async def test_a_failed_call_is_traced_with_its_error_and_counts_against_the_cap():
    def boom(messages):
        raise LLMRateLimited("429 slow down", retry_after=5)

    h = Harness(cap=1, inner=FakeClient(reply=boom))
    with pytest.raises(LLMRateLimited):
        await h.call(Grant("user", compile_log_id=9))
    (span,) = h.spans
    assert span["error"].startswith("LLMRateLimited") and "429" in span["error"]
    assert (span["compile_log_id"], span["input_tokens"], span["output_tokens"]) == (9, 0, 0)

    with pytest.raises(QuotaExceeded):                          # the failure used the allowance, so the cap is now reached
        await h.call()


async def test_a_broken_trace_store_does_not_turn_an_answer_into_an_error():
    h = Harness(save_raises=True)
    assert (await h.call()).text == "hello"


def test_quota_and_approval_errors_are_llm_errors():
    assert issubclass(QuotaExceeded, LLMError) and issubclass(ApprovalRequired, LLMError)


async def test_the_factory_only_hands_out_gated_clients():
    saved = []

    async def save(row):
        saved.append(row)

    async def count():
        return 0

    inner = FakeClient(["ok"])
    client = create_client(inner.profile, count_today=count, daily_call_cap=5, save_span=save, inner=inner)
    assert isinstance(client, ApprovedClient) and client.profile is inner.profile
    with pytest.raises(ApprovalRequired):
        await client.complete(MSGS, max_tokens=5)
    with approved(Grant("user")):
        await client.complete(MSGS, max_tokens=5)
    assert len(saved) == 1


# ---------- timeouts ----------

def test_the_factory_passes_the_configured_timeout_to_the_real_adapter():
    from llm.client import ModelProfile
    gemini = ModelProfile(name="g", provider="gemini", window=1000)

    async def count():
        return 0

    default = create_client(gemini, count_today=count, daily_call_cap=1, api_key="k")
    assert default._inner._timeout == 60.0                                  # the old behaviour when nothing is configured
    tuned = create_client(gemini, count_today=count, daily_call_cap=1, api_key="k", timeout=45)
    assert tuned._inner._timeout == 45


def test_two_attempts_finish_before_the_extension_gives_up():
    """A call may make two attempts (the chosen model, then the fallback). If the panel's timeout were shorter than that, it
    would report a failure while the server kept running (and spending) the call."""
    import re
    from pathlib import Path

    from config import MAX_ATTEMPT_TIMEOUT, Llm, load_config

    api_js = (Path(__file__).resolve().parent.parent / "extension1" / "shared" / "api.js").read_text(encoding="utf-8")
    extension_ms = int(re.search(r"MODEL_CALL_TIMEOUT_MS\s*=\s*(\d+)", api_js).group(1))
    assert extension_ms > 2 * MAX_ATTEMPT_TIMEOUT * 1000
    assert load_config().llm.attempt_timeout <= MAX_ATTEMPT_TIMEOUT
    with pytest.raises(ValueError):
        Llm(attempt_timeout=MAX_ATTEMPT_TIMEOUT + 1)
    assert "timeoutMs: MODEL_CALL_TIMEOUT_MS" in api_js            # and approveLLMCall actually uses it
