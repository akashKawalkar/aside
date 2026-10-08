# llm/replay.py — re-run a stored compile under a different recipe (or model) and show what changed.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from context.items import Item, Situation
from context.packer import PackResult, pack, render
from context.recipe import Recipe
from context.selector import make_selector
from llm.client import ModelProfile


@dataclass
class ReplayResult:
    recipe: Recipe
    text: str
    result: PackResult
    only_in_original: list[str]    # ids the stored compile packed that this recipe does not
    only_in_replay: list[str]      # ids this recipe packs that the stored compile did not


async def replay_compile(stored: dict[str, Any], recipe: Recipe, profile: ModelProfile, *, now: datetime | None = None) -> ReplayResult:
    """`stored` is a compile_log row. Items are rebuilt from the logged offer, so no live data is read;
    the same instant (`ts`) is used for validity windows unless `now` is given."""
    now = now or stored["ts"]
    items = [Item.from_dict(d) for d in stored["offered"]]
    situation = Situation(name=stored["situation"], query=stored.get("query") or "")

    selected, not_selected = await make_selector(recipe.selector).select(items, situation)
    result = pack(selected, recipe, profile, now=now)
    result.dropped = [*not_selected, *result.dropped]

    original = {c["id"] for c in stored["chosen"]}
    replayed = {i.id for i in result.chosen}
    return ReplayResult(
        recipe=recipe,
        text=render(result, recipe),
        result=result,
        only_in_original=sorted(original - replayed),
        only_in_replay=sorted(replayed - original),
    )
