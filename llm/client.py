# llm/client.py — the one interface everything else talks to, plus the model profile loaded from config/models.toml.
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from config import ROOT

MODELS_PATH = ROOT / "config" / "models.toml"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None             # on a "tool" message: which call it answers
    name: str | None = None
    tool_calls: tuple["ToolCall", ...] = ()     # on an "assistant" message: the calls it made


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Hidden reasoning ("thinking") tokens the provider spent but did not return. They are billed like output and they
    # count against the output limit, so a small max_tokens can leave NO room for the visible answer. Gemini does not
    # report them separately: they are total_tokens - prompt - completion (measured 2026-10-07, docs/gemini_api_findings.md).
    thinking_tokens: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    finish_reason: str = "stop"                  # "length" = cut off at max_tokens: the reply is incomplete
    tool_calls: tuple[ToolCall, ...] = ()
    fallback_from: str | None = None             # set when a fallback model answered because this one was busy or timed out


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    window: int
    input_cost: float = 0.0   # USD per million tokens
    output_cost: float = 0.0
    supports_tools: bool = False
    supports_json: bool = False
    chars_per_token: float = 4.0
    safety_margin: float = 1.15
    reasoning_effort: str | None = None          # sent as `reasoning_effort` when set; which values a model accepts differs per model

    def cost(self, usage: Usage) -> float:
        """Thinking tokens are billed as output."""
        return (usage.input_tokens * self.input_cost + (usage.output_tokens + usage.thinking_tokens) * self.output_cost) / 1_000_000


@runtime_checkable
class LLMClient(Protocol):
    profile: ModelProfile

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,   # provider-neutral: {"name", "description", "parameters": JSON schema}
        response_format: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion: ...


def load_profile(name: str | None = None, path: Path | None = None) -> ModelProfile:
    """The named profile, or the file's default (override with AGENT_MODEL)."""
    with open(path or MODELS_PATH, "rb") as f:
        data = tomllib.load(f)
    name = name or os.environ.get("AGENT_MODEL") or data["default"]
    try:
        return ModelProfile(name=name, **data["models"][name])
    except KeyError:
        raise ValueError(f"no model profile named {name!r} in {path or MODELS_PATH}") from None
