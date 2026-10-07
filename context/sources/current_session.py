# context/sources/current_session.py — what the user is doing right now: the request, plus any session text the caller passes.
from __future__ import annotations

from context.items import Item, Situation


class CurrentSessionSource:
    name = "current_session"

    async def fetch(self, situation: Situation) -> list[Item]:
        items = []
        if situation.query:
            items.append(Item(id="session:query", text=situation.query, source=self.name, priority=10,
                              provenance="user", core=True))
        for n, text in enumerate(situation.extra.get("session_text", [])):
            items.append(Item(id=f"session:{n}", text=text, source=self.name, provenance="user"))
        return items
