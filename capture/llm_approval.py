# capture/llm_approval.py — in-memory staging for LLM calls requiring user inspection and approval.
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

from llm.client import Message, ModelProfile


@dataclass
class PendingLLMCall:
    id: str
    created_at: datetime
    profile: ModelProfile
    messages: list[Message]
    context_text: str
    query: str
    tokens_estimate: int
    cost_estimate: float
    compile_log_id: int | None
    max_tokens: int = 2000
    temperature: float = 0.7
    kind: str = "chat"                          # what the reply is FOR: "chat" (shown as-is) or e.g. "schedule" (a finalizer turns it into something)
    meta: dict[str, Any] = field(default_factory=dict)
    response_format: dict[str, Any] | None = None
    status: str = "pending"  # pending | approved | rejected | completed | failed
    response_text: str | None = None
    error: str | None = None

    def to_preview(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "model": self.profile.name,
            "provider": self.profile.provider,
            "query": self.query,
            "context_text": self.context_text,
            "tokens_estimate": self.tokens_estimate,
            "cost_estimate_usd": self.cost_estimate,
            "compile_log_id": self.compile_log_id,
            "kind": self.kind,
            "status": self.status,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in self.messages
            ],
        }


# (call, completion, app) -> JSON-safe result. Runs after an approved call returns, for calls whose `kind` is registered.
Finalizer = Callable[[PendingLLMCall, Any, Any], Awaitable[dict[str, Any]]]


class ApprovalRegistry:
    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self._pending: dict[str, PendingLLMCall] = {}
        self._ttl_seconds = ttl_seconds
        self.finalizers: dict[str, Finalizer] = {}

    def register_finalizer(self, kind: str, finalizer: Finalizer) -> None:
        self.finalizers[kind] = finalizer

    def stage(
        self,
        *,
        profile: ModelProfile,
        messages: list[Message],
        context_text: str,
        query: str,
        tokens_estimate: int,
        compile_log_id: int | None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
        kind: str = "chat",
        meta: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> PendingLLMCall:
        self._cleanup()
        call_id = f"llm_appr_{uuid.uuid4().hex[:12]}"
        # Worst case: the whole prompt plus a reply that uses every allowed output token.
        cost = (tokens_estimate * profile.input_cost + max_tokens * profile.output_cost) / 1_000_000
        call = PendingLLMCall(
            id=call_id,
            created_at=datetime.now(timezone.utc),
            profile=profile,
            messages=messages,
            context_text=context_text,
            query=query,
            tokens_estimate=tokens_estimate,
            cost_estimate=cost,
            compile_log_id=compile_log_id,
            max_tokens=max_tokens,
            temperature=temperature,
            kind=kind,
            meta=meta or {},
            response_format=response_format,
        )
        self._pending[call_id] = call
        return call

    def get(self, call_id: str) -> PendingLLMCall | None:
        self._cleanup()
        return self._pending.get(call_id)

    def list_pending(self) -> list[PendingLLMCall]:
        self._cleanup()
        return [c for c in self._pending.values() if c.status == "pending"]

    def reject(self, call_id: str, reason: str = "User rejected") -> PendingLLMCall | None:
        call = self.get(call_id)
        if call:
            call.status = "rejected"
            call.error = reason
        return call

    def remove(self, call_id: str) -> None:
        self._pending.pop(call_id, None)

    def _cleanup(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            cid for cid, c in self._pending.items()
            if (now - c.created_at).total_seconds() > self._ttl_seconds
        ]
        for cid in expired:
            self._pending.pop(cid, None)

