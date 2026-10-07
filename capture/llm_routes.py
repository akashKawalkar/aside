# capture/llm_routes.py — approval and execution routes for staged LLM calls.
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel

from capture.llm_approval import ApprovalRegistry
from config import load_config
from llm.approved import ApprovalRequired, Grant, QuotaExceeded, approved
from llm.client import load_profile
from llm.factory import create_client
from llm.quota import provider_day_start
from storage import count_llm_traces_since, insert_llm_trace

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class RejectRequest(BaseModel):
    reason: str | None = None


async def calls_today(pool) -> int:
    """Model calls made in the provider's current day (it resets at midnight Pacific), failed ones included. Counted from
    llm_trace itself, so it survives a restart and needs no table of its own."""
    return await count_llm_traces_since(pool, provider_day_start(datetime.now(IST)))


def make_llm_router(op_ok, op_error, registry: ApprovalRegistry) -> APIRouter:
    router = APIRouter(prefix="/llm")

    @router.get("/pending")
    async def list_pending(request: Request):
        """List all LLM calls awaiting user approval."""
        pending = registry.list_pending()
        return op_ok(data={"items": [p.to_preview() for p in pending]})

    @router.get("/pending/{call_id}")
    async def get_pending_detail(request: Request, call_id: str):
        """View the exact context and messages that will be sent to the LLM."""
        call = registry.get(call_id)
        if not call:
            return op_error("No such pending call or expired.")
        return op_ok(data=call.to_preview())

    @router.post("/approve/{call_id}")
    async def approve_and_run(request: Request, call_id: str):
        """User approved this call: dispatch it to the LLM API and return the completion. The gated client records the
        trace (success or failure) and enforces the daily cap; this route only grants permission for this one call."""
        call = registry.get(call_id)
        if not call:
            return op_error("No such pending call or expired.")
        if call.status != "pending":
            return op_error(f"Call is already {call.status}.")

        pool = request.app.state.db_pool
        cfg = load_config().llm

        async def save_span(row):
            try:
                await insert_llm_trace(pool, row)
            except Exception:
                logger.warning("Could not persist LLM trace to database")

        fallback = None
        if cfg.fallback_model and cfg.fallback_model != call.profile.name:
            try:
                fallback = load_profile(cfg.fallback_model)
            except ValueError:
                logger.warning("fallback model %r has no profile in config/models.toml; running without a fallback", cfg.fallback_model)

        client = create_client(
            call.profile,
            count_today=lambda: calls_today(pool),
            daily_call_cap=cfg.daily_call_cap,
            background_enabled=cfg.background_enabled,
            save_span=save_span,
            timeout=cfg.attempt_timeout,
            fallback=fallback,
            fallback_inner=getattr(request.app.state, "llm_fallback_client", None),
            inner=getattr(request.app.state, "llm_client", None),   # tests substitute a FakeClient; still gated
        )

        try:
            with approved(Grant("user", operation=call.kind, compile_log_id=call.compile_log_id)):
                completion = await client.complete(
                    call.messages, response_format=call.response_format, max_tokens=call.max_tokens, temperature=call.temperature
                )
        except (QuotaExceeded, ApprovalRequired) as e:
            # Nothing was sent, so the call stays pending: raise the cap in config.toml and approve again.
            return op_error(str(e), data={"id": call.id})
        except Exception as e:
            logger.exception("Error executing approved LLM call %s", call.id)
            call.status = "failed"
            call.error = str(e)
            return op_error(f"LLM execution failed: {e}", data={"id": call.id})

        call.response_text = completion.text
        result = None
        finalizer = registry.finalizers.get(call.kind)
        if finalizer is not None:
            try:
                result = await finalizer(call, completion, request.app)
            except Exception as e:      # the call went out and is spent; say what was wrong with the reply
                logger.warning("finalizer for %s failed: %s", call.kind, e)
                call.status, call.error = "failed", str(e)
                return op_error(f"The model replied, but the reply could not be used: {e}",
                                data={"id": call.id, "raw_text": completion.text[:2000]})
        call.status = "completed"
        return op_ok(
            message="LLM call completed.",
            data={
                "id": call.id,
                "text": completion.text,
                "model": completion.model,
                "tokens_used": {
                    "input": completion.usage.input_tokens,
                    "output": completion.usage.output_tokens,
                },
                "latency_ms": completion.latency_ms,
                "compile_log_id": call.compile_log_id,
                "kind": call.kind,
                "finish_reason": completion.finish_reason,                 # "length" = the reply was cut off
                "thinking_tokens": completion.usage.thinking_tokens,
                "fell_back_from": completion.fallback_from,                # set if another model answered because this one was busy
                "result": result,
            },
        )

    @router.post("/reject/{call_id}")
    async def reject_call(request: Request, call_id: str, body: RejectRequest | None = None):
        """User rejected this LLM call."""
        reason = body.reason if body and body.reason else "User cancelled"
        call = registry.reject(call_id, reason=reason)
        if not call:
            return op_error("No such pending call or expired.")
        return op_ok(message="LLM call rejected.", data={"id": call.id, "status": "rejected"})

    return router
