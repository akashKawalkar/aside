# context/packer.py — greedy packing of items into a recipe's budgets, with an explicit reason for every drop.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from context.items import Dropped, Item
from context.recipe import Recipe
from llm.client import ModelProfile
from llm.tokens import estimate_tokens


@dataclass
class PackResult:
    chosen: list[Item] = field(default_factory=list)
    dropped: list[Dropped] = field(default_factory=list)
    budget: int = 0
    tokens: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _filter_reason(item: Item, recipe: Recipe, now: datetime) -> str | None:
    spec = recipe.spec(item.source)
    if spec is None:
        return "not_in_recipe"
    if item.status in spec.exclude_status:
        return f"filtered:{item.status}"
    if item.confidence < spec.min_confidence:
        return "low_confidence"
    if item.valid_to is not None and item.valid_to < now:
        return "expired"
    if item.valid_from is not None and item.valid_from > now:
        return "not_yet_valid"
    return None


def pack(items: list[Item], recipe: Recipe, profile: ModelProfile, *, now: datetime) -> PackResult:
    """Sources go in recipe-priority order, items by their own priority. An item that does not fit is
    dropped and the next, smaller one may still fit."""
    result = PackResult(budget=recipe.budget(profile.window))
    remaining: dict[str, list[Item]] = {}

    for item in items:
        if not item.tokens:
            item.tokens = estimate_tokens(item.text, profile)
        reason = _filter_reason(item, recipe, now)
        if reason:
            result.dropped.append(Dropped(item.id, item.source, reason, item.tokens))
        else:
            remaining.setdefault(item.source, []).append(item)

    for spec in recipe.ordered():
        cap = int(result.budget * spec.cap_fraction)
        used = 0
        # sorted() is stable, so equal priorities keep the order the source returned them in
        for n, item in enumerate(sorted(remaining.get(spec.name, []), key=lambda i: -i.priority)):
            if spec.max_items is not None and n >= spec.max_items:
                reason = "max_items"
            elif used + item.tokens > cap:
                reason = "source_cap"
            elif result.tokens + item.tokens > result.budget:
                reason = "over_budget"
            else:
                result.chosen.append(item)
                used += item.tokens
                result.tokens += item.tokens
                continue
            result.dropped.append(Dropped(item.id, item.source, reason, item.tokens))
        result.by_source[spec.name] = used

    return result


def _bullet(text: str) -> str:
    """One list item; continuation lines are indented so a multi-line text stays inside its bullet."""
    first, *rest = text.strip().splitlines() or [""]
    return "\n".join([f"- {first}", *(f"  {line}" if line.strip() else "" for line in rest)])


def render(result: PackResult, recipe: Recipe) -> str:
    """The text the model sees. Sources return plain text; formatting lives only here: one `##` block per source in
    recipe-priority order, a `###` sub-heading per item group (a persistent-file section, a skill's name) in order of
    first appearance, and a bullet per item."""
    blocks = []
    for spec in recipe.ordered():
        items = [i for i in result.chosen if i.source == spec.name]
        if not items:
            continue
        groups: dict[str | None, list[Item]] = {}
        for item in items:
            groups.setdefault(item.group, []).append(item)
        lines = [f"## {spec.name.replace('_', ' ')}"]
        for group, members in groups.items():
            if group:
                lines.append(f"### {group}")
            lines.extend(_bullet(m.text) for m in members)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
