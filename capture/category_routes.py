"""capture/category_routes.py — endpoints for managing and reviewing category rules."""
from __future__ import annotations

from typing import Callable, Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from digital.rules import CATEGORIES
import storage


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    label: str = Field(min_length=1, max_length=100)
    source: Literal["laptop", "phone"] | None = None
    app: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=200)
    title_has: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=1, le=100)
    use_title: bool = True
    note: str = Field(default="", max_length=500)


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=100)
    note: str | None = Field(default=None, max_length=500)


def make_category_router(op_ok: Callable, op_error: Callable) -> APIRouter:
    router = APIRouter(prefix="/categories", tags=["categories"])

    @router.get("/rules")
    async def list_rules(request: Request, review: bool = False):
        pool = request.app.state.db_pool
        rules = await storage.list_category_rules(pool, review_only=review)
        # Format dates as ISO strings
        out = []
        for r in rules:
            row = dict(r)
            if row.get("created_at"):
                row["created_at"] = row["created_at"].isoformat()
            out.append(row)
        return op_ok(data={"rules": out, "categories": list(CATEGORIES)})

    @router.post("/rules")
    async def create_rule(body: RuleCreate, request: Request):
        if body.category not in CATEGORIES:
            return op_error(f"Invalid category: {body.category}. Must be one of {', '.join(CATEGORIES)}.")
        pool = request.app.state.db_pool
        rule_id = await storage.create_category_rule(
            pool,
            category=body.category,
            label=body.label,
            app=body.app,
            domain=body.domain,
            title_has=body.title_has,
            source=body.source,
            priority=body.priority,
            use_title=body.use_title,
            note=body.note,
        )
        return op_ok(data={"id": rule_id}, message="Category rule created.")

    @router.post("/rules/{rule_id}/approve")
    async def approve_rule(rule_id: int, request: Request):
        pool = request.app.state.db_pool
        if not await storage.approve_category_rule(pool, rule_id):
            return op_error("Category rule not found.")
        return op_ok(message="Category rule approved.")

    @router.patch("/rules/{rule_id}")
    async def update_rule(rule_id: int, body: RuleUpdate, request: Request):
        if body.category is not None and body.category not in CATEGORIES:
            return op_error(f"Invalid category: {body.category}. Must be one of {', '.join(CATEGORIES)}.")
        pool = request.app.state.db_pool
        updated = await storage.update_category_rule(
            pool,
            rule_id,
            category=body.category,
            label=body.label,
            enabled=body.enabled,
            priority=body.priority,
            note=body.note,
        )
        if not updated:
            return op_error("Category rule not found.")
        return op_ok(message="Category rule updated.")

    @router.delete("/rules/{rule_id}")
    async def delete_rule(rule_id: int, request: Request):
        pool = request.app.state.db_pool
        if not await storage.delete_category_rule(pool, rule_id):
            return op_error("Category rule not found.")
        return op_ok(message="Category rule deleted.")

    return router
