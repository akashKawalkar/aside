# llm/trace.py — one record per model call, shaped after the OpenTelemetry GenAI semantic conventions
# (operation, provider, model, input/output tokens, finish reason). Stored in the llm_trace table.
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from llm.client import Completion, ModelProfile

OPERATION_NAME = "chat"   # all our model calls are chat completions; our own purpose label goes to aside.operation

SaveSpan = Callable[[dict[str, Any]], Awaitable[Any]]


def _new_id(nbytes: int) -> str:
    return uuid.uuid4().hex[: nbytes * 2]


@dataclass
class Span:
    operation: str                      # "chat", "extraction", "schedule", ...
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    error: str | None = None
    compile_log_id: int | None = None   # the context compile this call used
    attrs: dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: _new_id(16))
    span_id: str = field(default_factory=lambda: _new_id(8))
    parent_span_id: str | None = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def span_from_completion(
    completion: Completion,
    profile: ModelProfile,
    *,
    operation: str,
    compile_log_id: int | None = None,
    parent: Span | None = None,
    attrs: dict[str, Any] | None = None,
) -> Span:
    return Span(
        operation=operation,
        provider=profile.provider,
        model=completion.model,
        input_tokens=completion.usage.input_tokens,
        output_tokens=completion.usage.output_tokens,
        latency_ms=completion.latency_ms,
        finish_reason=completion.finish_reason,
        compile_log_id=compile_log_id,
        attrs={
            "cost_usd": profile.cost(completion.usage),
            "thinking_tokens": completion.usage.thinking_tokens,
            "truncated": completion.finish_reason == "length",      # the reply was cut off at max_tokens
            **({"fell_back_from": completion.fallback_from} if completion.fallback_from else {}),
            **(attrs or {}),
        },
        trace_id=parent.trace_id if parent else _new_id(16),
        parent_span_id=parent.span_id if parent else None,
    )


async def record(span: Span, *, save: SaveSpan | None = None) -> Span:
    """Persist through `save` (storage.insert_llm_trace bound to a pool). No `save` means keep nothing."""
    if save is not None:
        await save(span.to_row())
    return span


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _nanos(moment: datetime) -> int:
    """Exact integer nanoseconds since the epoch (a float would lose precision at this magnitude)."""
    return (moment - _EPOCH) // timedelta(microseconds=1) * 1000


def to_otel(span: "Span | dict[str, Any]") -> dict[str, Any]:
    """A span in OpenTelemetry GenAI shape, as a plain dict (no OTel SDK needed). `ts` is the END of the call, so the
    start is `ts - latency_ms`. Mapping table and caveats: docs/tracing.md."""
    row = span.to_row() if isinstance(span, Span) else span
    attrs = dict(row.get("attrs") or {})
    end_nanos = _nanos(row["ts"])
    start_nanos = end_nanos - round((row.get("latency_ms") or 0.0) * 1_000_000)

    out: dict[str, Any] = {
        "gen_ai.operation.name": OPERATION_NAME,
        "gen_ai.provider.name": row["provider"],
        "gen_ai.request.model": attrs.pop("request_model", row["model"]),
        "gen_ai.response.model": row["model"],
        "gen_ai.usage.input_tokens": row["input_tokens"],
        "gen_ai.usage.output_tokens": row["output_tokens"],
        "aside.operation": row["operation"],
    }
    if row.get("finish_reason"):
        out["gen_ai.response.finish_reasons"] = [row["finish_reason"]]
    if (max_tokens := attrs.pop("max_tokens", None)) is not None:
        out["gen_ai.request.max_tokens"] = max_tokens
    if (temperature := attrs.pop("temperature", None)) is not None:
        out["gen_ai.request.temperature"] = temperature
    if (response_id := attrs.pop("response_id", None)) is not None:
        out["gen_ai.response.id"] = response_id
    if row.get("compile_log_id") is not None:
        out["aside.compile_log_id"] = row["compile_log_id"]
    if (cost := attrs.pop("cost_usd", None)) is not None:
        out["aside.cost_usd"] = cost
    out.update({f"aside.attr.{key}": value for key, value in attrs.items()})

    status: dict[str, Any] = {"code": "OK"}
    if row.get("error"):
        out["error.type"] = "_OTHER"   # the spec wants a low-cardinality class; the free text goes in the status message
        status = {"code": "ERROR", "message": row["error"]}

    return {
        "name": f"{OPERATION_NAME} {out['gen_ai.request.model']}",
        "kind": "CLIENT",
        "trace_id": row["trace_id"],
        "span_id": row["span_id"],
        "parent_span_id": row.get("parent_span_id"),
        "start_time_unix_nano": start_nanos,
        "end_time_unix_nano": end_nanos,
        "status": status,
        "attributes": out,
    }
