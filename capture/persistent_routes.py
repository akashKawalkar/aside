# capture/persistent_routes.py — the settings editor for the persistent file, undo, and the hidden "mark wrong" gesture.
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from config import Memory
from context.persistent_schema import ALWAYS_ON, SECTIONS
from llm.tokens import estimate_tokens
from storage import (
    add_persistent_entry,
    confirm_persistent_entry,
    edit_persistent_entry,
    insert_mark_wrong,
    list_persistent_entries,
    retire_persistent_entry,
    undo_persistent_change,
)


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str
    text: str = Field(max_length=2000)   # the schema's own 500-character rule gives the friendly error
    source: Literal["user", "agent", "note", "pattern"] = "user"
    replaces_id: int | None = None


class EntryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=2000)
    section: str | None = None


class MarkWrong(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=100)   # e.g. "diff_log:12" or "reply"
    reason: str | None = Field(default=None, max_length=1000)
    compile_log_id: int | None = None


def _problem(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        return exc.errors()[0]["msg"].removeprefix("Value error, ")
    return str(exc)


def make_persistent_router(op_ok, op_error, memory: Memory) -> APIRouter:
    router = APIRouter()

    @router.get("/persistent-file")
    async def persistent_file(request: Request):
        rows = await list_persistent_entries(request.app.state.db_pool)
        sections = {name: {"always_on": name in ALWAYS_ON, "about": about, "entries": []} for name, (_, about) in SECTIONS.items()}
        for row in rows:
            sections[row["section"]]["entries"].append(row)
        live = sum(estimate_tokens(r["text"]) for r in rows if r["status"] != "retired")
        return op_ok(data={"sections": sections, "tokens": live, "cap_tokens": memory.cap_tokens, "settle_days": memory.settle_days})

    @router.post("/persistent-file/entries")
    async def add(request: Request, body: EntryCreate):
        try:
            out = await add_persistent_entry(request.app.state.db_pool, section=body.section, text=body.text,
                                             source=body.source, replaces_id=body.replaces_id, memory=memory)
        except ValueError as exc:
            return op_error(_problem(exc))
        return op_ok(message="Saved." if out["action"] == "added" else "Already there, counted as seen again.", data=out)

    @router.patch("/persistent-file/entries/{entry_id}")
    async def edit(request: Request, entry_id: int, body: EntryEdit):
        try:
            out = await edit_persistent_entry(request.app.state.db_pool, entry_id, text=body.text, section=body.section, memory=memory)
        except ValueError as exc:
            return op_error(_problem(exc))
        return op_ok(data=out) if out else op_error("No such entry.")

    @router.post("/persistent-file/entries/{entry_id}/confirm")
    async def confirm(request: Request, entry_id: int):
        try:
            out = await confirm_persistent_entry(request.app.state.db_pool, entry_id, memory=memory)
        except ValueError as exc:
            return op_error(_problem(exc))
        return op_ok(data=out) if out else op_error("No such entry.")

    @router.delete("/persistent-file/entries/{entry_id}")
    async def retire(request: Request, entry_id: int):
        try:
            out = await retire_persistent_entry(request.app.state.db_pool, entry_id)
        except ValueError as exc:
            return op_error(_problem(exc))
        return op_ok(data={"entry": out}) if out else op_error("No such entry.")

    @router.post("/persistent-file/undo/{diff_id}")
    async def undo(request: Request, diff_id: int):
        try:
            out = await undo_persistent_change(request.app.state.db_pool, diff_id)
        except ValueError as exc:
            return op_error(_problem(exc))
        return op_ok(message="Undone.", data=out) if out else op_error("No such change.")

    @router.post("/mark-wrong")
    async def mark_wrong(request: Request, body: MarkWrong):
        mark_id = await insert_mark_wrong(request.app.state.db_pool, target=body.target,
                                          compile_log_id=body.compile_log_id, reason=body.reason or None)
        return op_ok(data={"id": mark_id})

    return router
