# capture/schedule_gen_routes.py — schedule generation (plan 3.6): ask for a draft of tomorrow, revise it, edit/accept/
# discard its entries. The generate/revise routes call the model directly (capture/llm_run.py: user grant, daily cap, trace)
# and return the new draft. Request models are module-level on purpose: FastAPI cannot resolve annotations of local classes.
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

import storage
from capture import llm_run
from config import load_config
from context.recipe import load_recipe
from llm.approved import QuotaExceeded
from llm.client import load_profile
from schedule_gen import service
from schedule_gen.compare import draft_vs_final
from schedule_gen.model import IST, rules_from_config, tomorrow
from schedule_gen.parse import ParseError
from schedule_gen.service import DraftError, GenerationIncomplete

logger = logging.getLogger(__name__)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(default="", max_length=500)       # optional, e.g. "tomorrow is ekadashi"


class ReviseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=500)


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_at: datetime | None = None
    end_at: datetime | None = None


class IndexesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indexes: list[int] | None = Field(default=None, max_length=50)   # None = every pending entry / the whole draft


def make_schedule_gen_router(op_ok, op_error) -> APIRouter:
    router = APIRouter(prefix="/schedule")

    def now_of(request: Request) -> datetime:
        fn = getattr(request.app.state, "now_fn", None)
        return fn() if fn else datetime.now(IST)

    def rules_for(day: date):
        return rules_from_config(day, load_config().data_quality)

    async def make_draft(request: Request, instruction: str, revising: dict[str, Any] | None):
        app, pool = request.app, request.app.state.db_pool
        cfg = load_config()
        profile = getattr(app.state, "llm_profile", None) or load_profile()

        async def save_log(row):
            try:
                return await storage.insert_compile_log(pool, row)
            except Exception:
                logger.warning("Could not save compile log, proceeding without log_id")
                return None

        async def complete(messages, compile_log_id):
            return await llm_run.run_user_call(
                app, profile=profile, messages=messages, operation=service.KIND, compile_log_id=compile_log_id,
                max_tokens=service.GEN_MAX_TOKENS, temperature=service.GEN_TEMPERATURE, response_format={"type": "json_object"},
            )

        try:
            draft, completion = await service.draft_with_model(
                pool, complete, rules=rules_for(revising["target_day"] if revising else tomorrow(now_of(request))),
                profile=profile, recipe=load_recipe("schedule"), save_log=save_log, max_item_tokens=cfg.llm.max_item_tokens,
                instruction=instruction.strip(), revising=revising,
            )
        except QuotaExceeded as exc:
            return op_error(f"{exc}. Use the rule-based draft instead.")
        except (ParseError, GenerationIncomplete) as exc:      # the call went out and is spent; say what was wrong with the reply
            return op_error(f"The model replied, but the reply could not be used: {exc}")
        except Exception as exc:
            logger.exception("schedule draft call failed")
            return op_error(f"The model call failed: {exc}")
        return op_ok(message="Draft ready.", data={"draft": draft, "model": completion.model, "fell_back_from": completion.fallback_from})

    # ---------- state ----------

    @router.get("/draft/status")
    async def draft_status(request: Request):
        pool = request.app.state.db_pool
        state = await service.status(pool, now_of(request))
        cap = load_config().llm.daily_call_cap
        state["llm_available"] = await llm_run.calls_today(pool) < cap
        return op_ok(data=state)

    @router.get("/drafts")
    async def list_versions(request: Request, day: date | None = None, limit: int = 30):
        drafts = await storage.list_drafts(request.app.state.db_pool, day=day, limit=max(1, min(limit, 100)))
        return op_ok(data={"drafts": [service.draft_to_api(d) for d in drafts]})

    @router.get("/drafts/{day}/compare")
    async def compare(request: Request, day: date):
        """First generated draft vs the day's final schedule: the end-of-day snapshot, or the schedule as it stands until one exists."""
        pool = request.app.state.db_pool
        versions = await storage.list_drafts(pool, day=day)
        if not versions:
            return op_error("No draft was ever made for that day.")
        first = versions[-1]
        final = await storage.get_schedule_snapshot(pool, day=day)
        source = "snapshot"
        if final is None:
            start, end = service._day_bounds(day)
            final = [{**r, "start_at": r["start_at"].isoformat(), "end_at": r["end_at"].isoformat()}
                     for r in await storage.list_schedule_range(pool, start=start, end=end)]
            source = "live"
        return op_ok(data={"day": day.isoformat(), "first_version": first["version"], "final_source": source,
                           "comparison": draft_vs_final(first["proposed"], final)})

    @router.get("/draft/{draft_id}")
    async def get_one(request: Request, draft_id: int):
        draft = await storage.get_draft(request.app.state.db_pool, draft_id)
        return op_ok(data={"draft": service.draft_to_api(draft)}) if draft else op_error("No such draft.")

    # ---------- make a draft ----------

    @router.post("/draft/generate")
    async def generate(request: Request, body: GenerateRequest):
        try:
            await service._require_can_generate(request.app.state.db_pool, now_of(request))
            return await make_draft(request, body.instruction, None)
        except DraftError as exc:
            return op_error(str(exc))

    @router.post("/draft/placeholder")
    async def placeholder_draft(request: Request):
        try:
            day = tomorrow(now_of(request))
            draft = await service.create_placeholder_draft(request.app.state.db_pool, now_of(request), rules_for(day))
        except DraftError as exc:
            return op_error(str(exc))
        return op_ok(message="Rule-based draft ready.", data={"draft": draft})

    @router.post("/draft/{draft_id}/revise")
    async def revise(request: Request, draft_id: int, body: ReviseRequest):
        try:
            draft = await storage.get_draft(request.app.state.db_pool, draft_id)
            if draft is None or draft["status"] != "open":
                raise DraftError("Only an open draft can be revised.")
            return await make_draft(request, body.instruction, draft)
        except DraftError as exc:
            return op_error(str(exc))

    # ---------- what the user does with entries ----------

    @router.patch("/draft/{draft_id}/entries/{index}")
    async def patch_entry(request: Request, draft_id: int, index: int, body: EntryPatch):
        for value in (body.start_at, body.end_at):
            if value is not None and value.tzinfo is None:
                return op_error("Times must include a UTC offset.")
        try:
            draft = await storage.get_draft(request.app.state.db_pool, draft_id)
            if draft is None:
                raise DraftError("No such draft.")
            edited = await service.edit_entry(
                request.app.state.db_pool, draft_id, index,
                service.EntryEdit(body.title, body.start_at, body.end_at), rules_for(draft["target_day"]),
            )
        except DraftError as exc:
            return op_error(str(exc))
        return op_ok(data={"draft": edited})

    @router.post("/draft/{draft_id}/accept")
    async def accept(request: Request, draft_id: int, body: IndexesRequest):
        try:
            result = await service.accept(request.app.state.db_pool, draft_id, body.indexes)
        except DraftError as exc:
            return op_error(str(exc))
        note = f" {len(result['conflicts'])} clashed with something added since and stayed in the draft." if result["conflicts"] else ""
        return op_ok(message=f"Accepted {len(result['accepted'])}.{note}", data=result)

    @router.post("/draft/{draft_id}/discard")
    async def discard(request: Request, draft_id: int, body: IndexesRequest):
        try:
            draft = await service.discard(request.app.state.db_pool, draft_id, body.indexes)
        except DraftError as exc:
            return op_error(str(exc))
        return op_ok(data={"draft": draft})

    return router
