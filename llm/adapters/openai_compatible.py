# llm/adapters/openai_compatible.py — LLMClient for any server speaking the OpenAI chat-completions wire format
# (OpenAI, OpenRouter, Groq, Together, Ollama, Gemini's compatibility endpoint, ...). The HTTP layer is injected, so
# tests need no network and the transport can be swapped. Wire notes: https://platform.openai.com/docs/api-reference/chat
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from llm.client import Completion, Message, ModelProfile, ToolCall, Usage
from llm.errors import (
    LLMAuthError, LLMBadRequest, LLMRateLimited, LLMResponseError, LLMServerError, LLMTimeout, LLMUnavailable,
)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: Any                                    # parsed JSON, or None if the body was not JSON
    headers: Mapping[str, str] = field(default_factory=dict)


# (url, headers, json_body, timeout_seconds) -> HttpResponse. Raise TimeoutError / OSError for transport failures.
Post = Callable[[str, dict[str, str], dict[str, Any], float], Awaitable[HttpResponse]]


async def aiohttp_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> HttpResponse:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(url, headers=headers, json=body) as response:
            try:
                parsed = await response.json(content_type=None)
            except ValueError:
                parsed = None
            return HttpResponse(response.status, parsed, {k.lower(): v for k, v in response.headers.items()})


class OpenAICompatibleClient:
    def __init__(
        self,
        profile: ModelProfile,
        *,
        base_url: str,
        api_key: str | None = None,
        post: Post = aiohttp_post,
        timeout: float = 60.0,
    ) -> None:
        self.profile = profile
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._post = post
        self._timeout = timeout

    def __repr__(self) -> str:      # never show the key
        return f"OpenAICompatibleClient(model={self.profile.name!r}, url={self._url!r})"

    # ---------- request ----------

    @staticmethod
    def _message(m: Message) -> dict[str, Any]:
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.name:
            out["name"] = m.name
        if m.tool_call_id:
            out["tool_call_id"] = m.tool_call_id
        if m.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in m.tool_calls
            ]
            if not m.content:
                out["content"] = None
        return out

    def _body(self, messages, tools, response_format, max_tokens, temperature) -> dict[str, Any]:
        if tools and not self.profile.supports_tools:
            raise LLMBadRequest(f"model {self.profile.name!r} is not marked as supporting tools")
        if response_format and not self.profile.supports_json:
            raise LLMBadRequest(f"model {self.profile.name!r} is not marked as supporting JSON output")

        body: dict[str, Any] = {
            "model": self.profile.name,
            "messages": [self._message(m) for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self.profile.reasoning_effort:
            body["reasoning_effort"] = self.profile.reasoning_effort
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                  "parameters": t.get("parameters", {"type": "object", "properties": {}})}}
                for t in tools
            ]
        if response_format:
            if response_format.get("type") == "json_schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": response_format.get("name", "response"), "schema": response_format["schema"], "strict": True}}
            else:
                body["response_format"] = response_format        # {"type": "json_object"} passes straight through
        return body

    # ---------- response ----------

    @staticmethod
    def _error_text(body: Any) -> str:
        if isinstance(body, list) and len(body) > 0:
            body = body[0]
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str):
                return error
        return "no details"

    def _raise_for_status(self, response: HttpResponse) -> None:
        status, detail = response.status, self._error_text(response.body)
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise LLMAuthError(f"HTTP {status}: {detail}")
        if status == 429:
            try:
                retry_after = float(response.headers.get("retry-after", ""))
            except ValueError:
                retry_after = None
            raise LLMRateLimited(f"HTTP 429: {detail}", retry_after)
        if status >= 500:
            raise LLMServerError(f"HTTP {status}: {detail}")
        raise LLMBadRequest(f"HTTP {status}: {detail}")

    @staticmethod
    def _parse(body: Any, latency_ms: float, fallback_model: str) -> Completion:
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (TypeError, KeyError, IndexError):
            raise LLMResponseError("response has no choices[0].message") from None

        calls = []
        for raw in message.get("tool_calls") or []:
            try:
                arguments = json.loads(raw["function"]["arguments"] or "{}")
                calls.append(ToolCall(raw["id"], raw["function"]["name"], arguments))
            except (KeyError, TypeError, ValueError):
                raise LLMResponseError("a tool call is malformed or its arguments are not valid JSON") from None

        usage = body.get("usage") or {}
        prompt, completion = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        # Gemini hides its reasoning tokens: they appear only as the gap between the total and the two visible parts.
        thinking = max(0, int(usage.get("total_tokens") or 0) - prompt - completion) if usage.get("total_tokens") else 0
        return Completion(
            text=message.get("content") or "",
            model=body.get("model") or fallback_model,
            usage=Usage(prompt, completion, thinking),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason") or "stop",
            tool_calls=tuple(calls),
        )

    # ---------- the one public call ----------

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        max_tokens: int,
        temperature: float = 0.0,
    ) -> Completion:
        body = self._body(messages, tools, response_format, max_tokens, temperature)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.perf_counter()
        try:
            response = await self._post(self._url, headers, body, self._timeout)
        except (TimeoutError, asyncio.TimeoutError):
            raise LLMTimeout(f"no answer within {self._timeout:g}s") from None
        except OSError as e:
            raise LLMUnavailable(f"could not reach {self._url}: {type(e).__name__}") from None
        latency_ms = (time.perf_counter() - started) * 1000

        self._raise_for_status(response)
        return self._parse(response.body, latency_ms, self.profile.name)
