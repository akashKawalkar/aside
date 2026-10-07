# capture/schedule_gen_routes.py — schedule generation (plan 3.6): ask for a draft of tomorrow, revise it, edit/accept/
# discard its entries. A model-made draft goes through the same approval card as chat: the generate/revise routes only
# STAGE a call; the user's approval (POST /llm/approve) is what sends it, and the finalizer registered below turns the
# reply into a draft. Request models are module-level on purpose: FastAPI cannot resolve annotations of local classes.
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

import capture.llm_routes as llm_routes
import storage
from capture.llm_approval import ApprovalRegistry
from config import load_config
from context.recipe import load_recipe
from llm.client import load_profile
from schedule_gen import service
from schedule_gen.compare import draft_vs_final
from schedule_gen.model import IST, rules_from_config, tomorrow
from schedule_gen.service import DraftError

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


def make_schedule_gen_router(op_ok, op_error, registry: ApprovalRegistry) -> APIRouter:
    router = APIRouter(prefix="/schedule")

    def now_of(request: Request) -> datetime:
        fn = getattr(request.app.state, "now_fn", None)
        return fn() if fn else datetime.now(IST)

    def rules_for(day: date):
        return rules_from_config(day, load_config().data_quality)

    async def finalize(call, completion, app) -> dict[str, Any]:
        return await service.finalize_generation(app.state.db_pool, call, completion, rules_for(date.fromisoformat(call.meta["day"])))

    registry.register_finalizer(service.KIND, finalize)

    async def stage(request: Request, instruction: str, revising: dict[str, Any] | None):
        pool = request.app.state.db_pool
        cfg = load_config()
        if await llm_routes.calls_today(pool) >= cfg.llm.daily_call_cap:
            raise DraftError("The daily model-call cap is used up. Use the rule-based draft, or raise [llm] daily_call_cap in config.toml.")

        async def save_log(row):
            try:
                return await storage.insert_compile_log(pool, row)
            except Exception:
                logger.warning("Could not save compile log, proceeding without log_id")
                return None

        day = tomorrow(now_of(request))
        call = await service.stage_generation(
            pool=pool, registry=registry, profile=getattr(request.app.state, "llm_profile", None) or load_profile(),
            recipe=load_recipe("schedule"), rules=rules_for(day), instruction=instruction, save_log=save_log,
            max_item_tokens=cfg.llm.max_item_tokens, revising=revising,
        )
        return op_ok(
            message="Draft prepared. Review the prompt and approve to send it.",
            data={"destination": "schedule_draft", "approval_required": True, "pending_id": call.id, "preview": call.to_preview()},
        )

    # ---------- state ----------

    @router.get("/draft/status")
    async def draft_status(request: Request):
        pool = request.app.state.db_pool
        state = await service.status(pool, now_of(request))
        cap = load_config().llm.daily_call_cap
        state["llm_available"] = await llm_routes.calls_today(pool) < cap
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
            return await stage(request, body.instruction, None)
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
            return await stage(request, body.instruction, draft)
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
