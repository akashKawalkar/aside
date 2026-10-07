# context/selector.py — optional pre-selection of items, separate from packing so it can be removed.
# off: everything goes to the packer. rules: situation tags. llm: a small model over descriptors (not built yet).
from __future__ import annotations

from typing import Protocol

from context.items import Dropped, Item, Situation


class Selector(Protocol):
    def select(self, items: list[Item], situation: Situation) -> tuple[list[Item], list[Dropped]]: ...


class OffSelector:
    def select(self, items, situation):
        return list(items), []


class RulesSelector:
    """Core items always stay. An item that names situations stays only in those."""

    def select(self, items, situation):
        kept, dropped = [], []
        for item in items:
            if item.core or not item.situations or situation.name in item.situations or set(item.situations) & set(situation.tags):
                kept.append(item)
            else:
                dropped.append(Dropped(item.id, item.source, "selector", item.tokens))
        return kept, dropped


class LLMSelector:
    """Slot for the descriptor-only small-model selector; falls back to rules when it is built."""

    def select(self, items, situation):
        raise NotImplementedError("the llm selector is not built yet; use 'off' or 'rules'")


def make_selector(mode: str) -> Selector:
    try:
        return {"off": OffSelector, "rules": RulesSelector, "llm": LLMSelector}[mode]()
    except KeyError:
        raise ValueError(f"unknown selector mode {mode!r}") from None
