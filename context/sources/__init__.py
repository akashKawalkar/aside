# context/sources — one module per source. A Source turns some part of the system into Items.
from __future__ import annotations

from typing import Protocol

from context.items import Item, Situation


class Source(Protocol):
    name: str

    async def fetch(self, situation: Situation) -> list[Item]: ...


class EmptySource:
    """Placeholder for sources whose data has not been wired yet."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def fetch(self, situation: Situation) -> list[Item]:
        return []
