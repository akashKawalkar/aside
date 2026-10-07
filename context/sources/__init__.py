# context/sources — one module per source. A Source turns some part of the system into Items.
from __future__ import annotations

from typing import Protocol

from context.items import Item, Situation


class Source(Protocol):
    name: str

    async def fetch(self, situation: Situation) -> list[Item]: ...


class EmptySource:
    """Placeholder for a source whose data does not exist yet (persistent file, skills, notes, history,
    observations arrive in M3/M5). A recipe may already name it."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def fetch(self, situation: Situation) -> list[Item]:
        return []
