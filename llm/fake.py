# llm/fake.py — deterministic stand-in for a provider. Used by every test and eval until a real adapter exists.
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from llm.client import Completion, Message, ModelProfile, ToolCall, Usage
from llm.tokens import estimate_messages, estimate_tokens


@dataclass
class RecordedCall:
    messages: list[Message]
    tools: list[dict[str, Any]] | None
    response_format: dict[str, Any] | None
    max_tokens: int
    temperature: float


class FakeClient:
    """Replies come from `script` (consumed in order; the last one repeats), or from `reply(messages)`.
    With neither, it answers `ok`. Every call is recorded in `.calls`."""

    def __init__(
        self,
        script: Iterable[str | Completion | ToolCall] = (),
        *,
        reply: Callable[[list[Message]], str] | None = None,
        profile: ModelProfile | None = None,
    ) -> None:
        self.profile = profile or ModelProfile(name="fake", provider="fake", window=8000, supports_tools=True, supports_json=True)
        self.calls: list[RecordedCall] = []
        self._script = list(script)
        self._reply = reply
        self._next = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        self.calls.append(RecordedCall(list(messages), tools, response_format, max_tokens, temperature))
        item: str | Completion | ToolCall
        if self._script:
            item = self._script[min(self._next, len(self._script) - 1)]
            self._next += 1
        elif self._reply:
            item = self._reply(messages)
        else:
            item = "ok"

        if isinstance(item, Completion):
            return item
        usage_in = estimate_messages(messages, self.profile)
        if isinstance(item, ToolCall):
            return Completion("", self.profile.name, Usage(usage_in, 10), 0.0, "tool_calls", (item,))
        return Completion(item, self.profile.name, Usage(usage_in, estimate_tokens(item, self.profile)), 0.0, "stop")
