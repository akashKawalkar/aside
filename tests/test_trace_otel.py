from __future__ import annotations

from datetime import datetime, timedelta, timezone

from llm.client import Completion, ModelProfile, Usage
from llm.trace import Span, span_from_completion, to_otel

END = datetime(2026, 10, 6, 12, 0, 5, tzinfo=timezone.utc)


def span(**kw) -> Span:
    base = dict(operation="chat", provider="anthropic", model="m-1", input_tokens=120, output_tokens=30, latency_ms=1500.0,
                finish_reason="stop", ts=END, trace_id="a" * 32, span_id="b" * 16)
    return Span(**{**base, **kw})


def test_core_mapping_and_timing():
    out = to_otel(span(compile_log_id=7, attrs={"cost_usd": 0.002}))
    attrs = out["attributes"]
    assert out["name"] == "chat m-1" and out["kind"] == "CLIENT"
    assert (out["trace_id"], out["span_id"], out["parent_span_id"]) == ("a" * 32, "b" * 16, None)
    assert out["end_time_unix_nano"] - out["start_time_unix_nano"] == 1_500_000_000
    assert out["end_time_unix_nano"] == int(END.timestamp()) * 1_000_000_000
    assert out["status"] == {"code": "OK"}
    assert attrs == {
        "gen_ai.operation.name": "chat", "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "m-1", "gen_ai.response.model": "m-1",
        "gen_ai.usage.input_tokens": 120, "gen_ai.usage.output_tokens": 30,
        "gen_ai.response.finish_reasons": ["stop"],
        "aside.operation": "chat", "aside.compile_log_id": 7, "aside.cost_usd": 0.002,
    }


def test_our_purpose_label_does_not_replace_the_standard_operation_name():
    attrs = to_otel(span(operation="extraction"))["attributes"]
    assert attrs["gen_ai.operation.name"] == "chat" and attrs["aside.operation"] == "extraction"


def test_optional_request_attributes_come_from_attrs_and_the_rest_is_namespaced():
    attrs = to_otel(span(attrs={"request_model": "m-1-latest", "max_tokens": 200, "temperature": 0.2, "response_id": "r1", "note": "x"}))["attributes"]
    assert attrs["gen_ai.request.model"] == "m-1-latest" and attrs["gen_ai.response.model"] == "m-1"
    assert (attrs["gen_ai.request.max_tokens"], attrs["gen_ai.request.temperature"], attrs["gen_ai.response.id"]) == (200, 0.2, "r1")
    assert attrs["aside.attr.note"] == "x" and "max_tokens" not in attrs and "aside.attr.max_tokens" not in attrs


def test_an_error_sets_error_type_and_status_message():
    out = to_otel(span(error="timed out after 30s", finish_reason=None))
    assert out["status"] == {"code": "ERROR", "message": "timed out after 30s"}
    assert out["attributes"]["error.type"] == "_OTHER" and "gen_ai.response.finish_reasons" not in out["attributes"]


def test_a_stored_row_maps_the_same_as_the_span_it_came_from():
    s = span(parent_span_id="c" * 16, attrs={"cost_usd": 0.5})
    assert to_otel(s.to_row()) == to_otel(s)
    assert to_otel(s)["parent_span_id"] == "c" * 16


def test_a_span_built_from_a_completion_maps_end_to_end():
    profile = ModelProfile(name="m-1", provider="anthropic", window=1000, input_cost=3.0, output_cost=15.0)
    done = Completion("hi", "m-1", Usage(1000, 200), latency_ms=800.0, finish_reason="stop")
    out = to_otel(span_from_completion(done, profile, operation="chat", compile_log_id=4))
    assert out["attributes"]["gen_ai.usage.input_tokens"] == 1000
    assert out["attributes"]["aside.cost_usd"] == (1000 * 3.0 + 200 * 15.0) / 1_000_000
    assert out["end_time_unix_nano"] - out["start_time_unix_nano"] == 800_000_000
