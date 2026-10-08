# capture/context_routes.py — read-only routes behind the settings Context viewer, plus replay under another recipe.
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from context.recipe import load_recipes
from llm.client import load_profile
from llm.replay import replay_compile
from storage import get_compile_log, list_compile_logs


def _breakdown(row: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per source: how many items were offered, packed and dropped, and the tokens packed."""
    table: dict[str, dict[str, int]] = {}
    for item in row["offered"]:
        table.setdefault(item["source"], {"offered": 0, "chosen": 0, "dropped": 0, "tokens": 0})["offered"] += 1
    for item in row["chosen"]:
        entry = table.setdefault(item["source"], {"offered": 0, "chosen": 0, "dropped": 0, "tokens": 0})
        entry["chosen"] += 1
        entry["tokens"] += item["tokens"]
    for item in row["dropped"]:
        table.setdefault(item["source"], {"offered": 0, "chosen": 0, "dropped": 0, "tokens": 0})["dropped"] += 1
    return table


def make_context_router(op_ok, op_error) -> APIRouter:
    router = APIRouter(prefix="/context")

    @router.get("/compiles")
    async def compiles(request: Request, limit: int = 50):
        return op_ok(data={"items": await list_compile_logs(request.app.state.db_pool, limit=min(max(limit, 1), 200))})

    @router.get("/compiles/{log_id}")
    async def compile_detail(request: Request, log_id: int):
        row = await get_compile_log(request.app.state.db_pool, log_id)
        if row is None:
            return op_error("No such compile.")
        text = {i["id"]: i["text"] for i in row["offered"]}
        row["chosen"] = [{**c, "text": text.get(c["id"], "")} for c in row["chosen"]]
        row["by_source"] = _breakdown(row)
        del row["offered"]
        return op_ok(data=row)

    @router.post("/compiles/{log_id}/replay")
    async def replay(request: Request, log_id: int, recipe: str):
        recipes = load_recipes()
        if recipe not in recipes:
            return op_error(f"No recipe named {recipe!r}.", detail=f"Available: {', '.join(sorted(recipes))}")
        row = await get_compile_log(request.app.state.db_pool, log_id)
        if row is None:
            return op_error("No such compile.")
        out = await replay_compile(row, recipes[recipe], load_profile())
        return op_ok(data={
            "recipe": recipe, "text": out.text, "tokens": out.result.tokens, "budget": out.result.budget,
            "by_source": out.result.by_source,
            "dropped": [d.to_dict() for d in out.result.dropped],
            "only_in_original": out.only_in_original, "only_in_replay": out.only_in_replay,
        })

    return router
