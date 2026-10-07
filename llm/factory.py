# llm/factory.py — builds an LLMClient for a given profile or model name.
from __future__ import annotations

import os
from dotenv import load_dotenv

from llm.adapters.openai_compatible import OpenAICompatibleClient
from llm.approved import ApprovedClient, CountToday
from llm.client import LLMClient, ModelProfile, load_profile
from llm.fake import FakeClient
from llm.resilient import FallbackClient
from llm.trace import SaveSpan

load_dotenv()


def _raw_client(profile: ModelProfile | str | None = None, *, api_key: str | None = None, timeout: float = 60.0) -> LLMClient:
    """The unguarded adapter. Private on purpose: everything outside this module gets it through `create_client`,
    which wraps it in the approval/quota gate."""
    if isinstance(profile, str) or profile is None:
        prof = load_profile(profile)
    else:
        prof = profile

    if prof.provider == "fake":
        return FakeClient(profile=prof)

    if prof.provider == "gemini":
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        # Clean potential quotes / export prefixes from .env
        clean_key = key.strip().strip("'\"")
        base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
        return OpenAICompatibleClient(prof, base_url=base_url, api_key=clean_key, timeout=timeout)

    # General OpenAI-compatible fallback
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    return OpenAICompatibleClient(prof, base_url=base_url, api_key=key.strip().strip("'\""), timeout=timeout)


def create_client(
    profile: ModelProfile | str | None = None,
    *,
    count_today: CountToday,
    daily_call_cap: int,
    background_enabled: bool = False,
    save_span: SaveSpan | None = None,
    inner: LLMClient | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
    fallback: ModelProfile | str | None = None,
    fallback_inner: LLMClient | None = None,
) -> ApprovedClient | FallbackClient:
    """A client that runs only inside `llm.approved.approved(...)`, only under today's cap, and always traces.
    Provider by profile: fake -> FakeClient, gemini -> Google's OpenAI-compatible endpoint, else OpenAI-compatible.
    `timeout` is how long ONE attempt waits for a reply. With `fallback`, a second, different model is tried when the first
    is overloaded or silent (see llm/resilient.py); each attempt is gated and traced on its own.
    `inner` / `fallback_inner` substitute already-built clients (tests pass FakeClients) and are still gated; with `inner`
    given and no `fallback_inner`, no fallback is built, so a test can never reach the network by accident."""
    def gated(client: LLMClient) -> ApprovedClient:
        return ApprovedClient(client, count_today=count_today, daily_call_cap=daily_call_cap,
                              background_enabled=background_enabled, save_span=save_span)

    primary = gated(inner if inner is not None else _raw_client(profile, api_key=api_key, timeout=timeout))

    if inner is not None:
        backup = fallback_inner
    elif fallback is not None:
        backup = _raw_client(fallback, api_key=api_key, timeout=timeout)
    else:
        backup = None
    if backup is None or backup.profile.name == primary.profile.name:
        return primary
    return FallbackClient(primary, gated(backup))
