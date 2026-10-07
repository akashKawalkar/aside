# context/usage.py — which context pieces a reply actually used. Interface and a no-op only for now.
# Planned mechanism: the reply returns the ids it used; fallback is local embedding overlap.
from __future__ import annotations

from typing import Protocol


class UsageTracker(Protocol):
    enabled: bool

    async def record(self, compile_log_id: int, used_ids: list[str]) -> None: ...


class NoopUsage:
    """Toggleable with no side effects: `record` does nothing either way."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    async def record(self, compile_log_id: int, used_ids: list[str]) -> None:
        return None
