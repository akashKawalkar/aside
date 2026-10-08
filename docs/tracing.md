# Tracing

Every model call produces one **span**, stored in the `llm_trace` table (`llm/trace.py`, repo `storage/repo/llm_trace.py`).
The span is shaped after the OpenTelemetry GenAI semantic conventions so it can be exported to any OpenTelemetry
backend later (ASIDE_PLAN.md A3) without reshaping. Nothing is exported today: the table is the source of truth.

`llm.trace.to_otel(span_or_row)` turns one of our spans into an OpenTelemetry-shaped dict (no SDK dependency).

## Status of the standard, and what was verified

The GenAI conventions are marked **Development** (not stable), and have moved from the main semantic-conventions
repository to `open-telemetry/semantic-conventions-genai`. Attribute names have been renamed before (for example
`gen_ai.system` became `gen_ai.provider.name`; `prompt_tokens`/`completion_tokens` became `input_tokens`/`output_tokens`).

- **Confirmed from current sources when this was written (2026-10):** `gen_ai.provider.name`, `gen_ai.operation.name`,
  `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.response.finish_reasons`, and that prompt/response
  content is opt-in, never default.
- **From memory of the spec, not re-read in this session:** the remaining names below (`gen_ai.response.model`,
  `gen_ai.response.id`, `gen_ai.request.max_tokens`, `gen_ai.request.temperature`, `gen_ai.usage.output_tokens`,
  `error.type`, the span name format `"{operation} {model}"`, span kind `CLIENT`).
- **Before wiring a real exporter, re-check this table against the spec.** `tests/test_trace_otel.py` pins the
  current mapping, so a rename shows up as a one-line change here and there.

## Our fields to OpenTelemetry

| Our field (`llm_trace` / `Span`) | OpenTelemetry | Notes |
|---|---|---|
| `trace_id`, `span_id`, `parent_span_id` | span context ids | 32 / 16 hex characters; `parent_span_id` is null for a root |
| `ts`, `latency_ms` | `start_time`, `end_time` | **`ts` is the end of the call**; start = `ts - latency_ms` |
| (always `chat`) | `gen_ai.operation.name` | Must be a well-known value. Every call we make is a chat completion |
| `operation` (`chat`, `extraction`, `schedule`, ...) | `aside.operation` | Our own purpose label; not part of the standard |
| `provider` | `gen_ai.provider.name` | |
| `model` | `gen_ai.response.model` and `gen_ai.request.model` | We store the model the provider reported. `attrs.request_model` overrides the request side |
| `input_tokens` | `gen_ai.usage.input_tokens` | |
| `output_tokens` | `gen_ai.usage.output_tokens` | |
| `finish_reason` | `gen_ai.response.finish_reasons` | An array in the standard, so `["stop"]` |
| `error` | `error.type` and span status | `error.type` is `_OTHER` (the standard wants a low-cardinality class); the text becomes the status message |
| `compile_log_id` | `aside.compile_log_id` | Links the call to the context compile that built its prompt |
| `attrs.cost_usd` | `aside.cost_usd` | The standard has no cost attribute |
| `attrs.max_tokens`, `attrs.temperature`, `attrs.response_id` | `gen_ai.request.max_tokens`, `gen_ai.request.temperature`, `gen_ai.response.id` | Present only if the caller put them in `attrs`; not stored by default |
| any other `attrs.<key>` | `aside.attr.<key>` | |
| span name | `"chat {model}"` | |
| span kind | `CLIENT` | |

## What is deliberately not recorded

- **Prompts and replies.** The standard makes content opt-in, and `llm_trace` has no column for it. What the model was
  shown is already recoverable: the call links to `compile_log`, which holds the offered, chosen and dropped context.
  Journal text is personal, so it is not copied into a second table.
- **Tool calls** are not separate spans yet. When the chat agent exists (post-API), each tool call becomes a child span
  with `gen_ai.operation.name = "execute_tool"` and the tool's name, sharing the call's `trace_id`.

## Retention

`llm_trace` rows are pruned after `retention.llm_trace_days` (default 180) by `python -m capture prune --apply`.
