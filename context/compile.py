# context/compile.py — fetch from the recipe's sources, select, pack, render, and log the whole decision.
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from context.gate import gate_text
from context.items import Dropped, Item, Situation
from context.packer import PackResult, pack, render
from context.recipe import Recipe
from context.selector import Selector, make_selector
from context.sources import Source
from llm.client import ModelProfile
from llm.tokens import estimate_tokens

SaveLog = Callable[[dict[str, Any]], Awaitable[int | None]]   # storage.insert_compile_log bound to a pool


@dataclass
class Compiled:
    id: int | None
    text: str
    recipe: Recipe
    result: PackResult
    log: dict[str, Any]


def build_log(
    situation: Situation, recipe: Recipe, profile: ModelProfile, offered: list[Item], result: PackResult,
    dropped: list[Dropped], ts: datetime,
) -> dict[str, Any]:
    return {
        "ts": ts,
        "situation": situation.name,
        "recipe_name": recipe.name,
        "recipe_hash": recipe.hash,
        "model": profile.name,
        "offered": [i.to_dict() for i in offered],       # full text, so a compile can be replayed under another recipe
        "chosen": [{"id": i.id, "source": i.source, "tokens": i.tokens} for i in result.chosen],
        "dropped": [d.to_dict() for d in dropped],
        "tokens": result.tokens,
        "query": situation.query,
    }


async def compile_context(
    situation: Situation,
    recipe: Recipe,
    sources: Mapping[str, Source],
    profile: ModelProfile,
    *,
    selector: Selector | None = None,
    save_log: SaveLog | None = None,
    now: datetime | None = None,
    max_item_tokens: int | None = None,
) -> Compiled:
    now = now or datetime.now(timezone.utc)
    missing = [s.name for s in recipe.sources if s.name not in sources]
    if missing:
        raise ValueError(f"recipe {recipe.name!r} names sources that were not provided: {missing}")

    fetched = await asyncio.gather(*(sources[s.name].fetch(situation) for s in recipe.sources), return_exceptions=True)

    offered: list[Item] = []
    dropped: list[Dropped] = []
    for spec, outcome in zip(recipe.sources, fetched):
        if isinstance(outcome, BaseException):      # one broken source must not take the whole context down
            dropped.append(Dropped(f"source:{spec.name}", spec.name, "source_error"))
            continue
        for item in outcome:
            if max_item_tokens is not None:     # the result gate: one oversized item must not eat a source's whole slice
                gated = gate_text(item.text, max_item_tokens, profile)
                if gated != item.text:
                    item.text, item.tokens = gated, 0
            item.tokens = item.tokens or estimate_tokens(item.text, profile)
        offered.extend(outcome)

    selected, not_selected = (selector or make_selector(recipe.selector)).select(offered, situation)
    dropped.extend(not_selected)

    result = pack(selected, recipe, profile, now=now)
    dropped.extend(result.dropped)
    result.dropped = dropped

    log = build_log(situation, recipe, profile, offered, result, dropped, now)
    log_id = await save_log(log) if save_log else None
    return Compiled(log_id, render(result, recipe), recipe, result, log)
