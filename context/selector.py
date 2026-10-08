# context/selector.py — optional pre-selection of items, separate from packing so it can be removed.
# off: everything goes to the packer. rules: situation tags. llm: a small model over descriptors (not built yet).
from __future__ import annotations

from typing import Protocol

from context.items import Dropped, Item, Situation


class Selector(Protocol):
    async def select(self, items: list[Item], situation: Situation) -> tuple[list[Item], list[Dropped]]: ...


class OffSelector:
    async def select(self, items, situation):
        return list(items), []


class RulesSelector:
    """Core items always stay. An item that names situations stays only in those."""

    async def select(self, items, situation):
        kept, dropped = [], []
        for item in items:
            if item.core or not item.situations or situation.name in item.situations or set(item.situations) & set(situation.tags):
                kept.append(item)
            else:
                dropped.append(Dropped(item.id, item.source, "selector", item.tokens))
        return kept, dropped


class LLMSelector:
    """Slot for the descriptor-only small-model selector; falls back to rules if missing client."""
    def __init__(self, client=None):
        self.client = client

    async def select(self, items, situation):
        if not self.client:
            return await RulesSelector().select(items, situation)
            
        # Separate core items and optional items
        core, optional = [], []
        for item in items:
            if item.core:
                core.append(item)
            else:
                optional.append(item)
                
        if not optional:
            return core, []
            
        # Build prompt using item descriptors
        from llm.client import Message
        import json
        
        system_prompt = (
            "You are a context selection agent. Your job is to select the most relevant optional context items "
            "for the user's current situation and query.\n"
            "Return ONLY a JSON array of the string IDs of the items you select. Do not select items that are irrelevant."
        )
        
        user_content = f"Situation: {situation.name}\n"
        if situation.query:
            user_content += f"Query: {situation.query}\n"
        user_content += "\nOptional Items available:\n"
        
        for item in optional:
            # Provide a truncated descriptor to the small LLM
            preview = item.text[:100].replace('\n', ' ')
            user_content += f"- ID: {item.id} | Source: {item.source} | Preview: {preview}...\n"
            
        try:
            completion = await self.client.complete(
                [Message("system", system_prompt), Message("user", user_content)],
                max_tokens=200
            )
            
            selected_ids = json.loads(completion.text)
            if not isinstance(selected_ids, list):
                selected_ids = []
            
            kept, dropped = [], []
            for item in items:
                if item.core or item.id in selected_ids:
                    kept.append(item)
                else:
                    dropped.append(Dropped(item.id, item.source, "selector", item.tokens))
            return kept, dropped
            
        except Exception:
            # Fallback to rules if the LLM fails
            return await RulesSelector().select(items, situation)



def make_selector(mode: str) -> Selector:
    try:
        return {"off": OffSelector, "rules": RulesSelector, "llm": LLMSelector}[mode]()
    except KeyError:
        raise ValueError(f"unknown selector mode {mode!r}") from None
