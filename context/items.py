# context/items.py — the unit the whole context framework moves around.
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class Item:
    """One piece of context that is packed or dropped whole."""
    id: str                          # stable, e.g. "task:12"; what usage tracking and replay refer to
    text: str
    source: str                      # which Source produced it
    priority: int = 0                # order within its source (higher first); source order comes from the recipe
    tokens: int = 0                  # 0 = not yet estimated; the packer fills it in
    provenance: str = ""             # where it came from ("schedule:7", "note:3", "user", "agent")
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    status: str = "active"           # active | provisional | shelved | retired
    core: bool = False               # always kept by the selector
    situations: tuple[str, ...] = () # empty = relevant everywhere; else only these situations (rules selector)
    descriptor: str = ""             # short label the (future) LLM selector may see instead of the text
    group: str | None = None         # sub-heading within its source (a persistent-file section, a skill's name); render() owns formatting
    tags: tuple[str, ...] = ()       # metadata for filtering (e.g. privacy layer)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["situations"] = list(self.situations)
        for key in ("valid_from", "valid_to"):
            d[key] = d[key].isoformat() if d[key] else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Item:
        d = dict(d)
        d["situations"] = tuple(d.get("situations") or ())
        for key in ("valid_from", "valid_to"):
            d[key] = datetime.fromisoformat(d[key]) if d.get(key) else None
        return cls(**d)


@dataclass(frozen=True)
class Dropped:
    id: str
    source: str
    reason: str                      # filtered:<status> | expired | not_yet_valid | low_confidence | not_in_recipe
                                     # | max_items | source_cap | over_budget | selector | source_error
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Situation:
    """What the context is being built for."""
    name: str                        # "chat", "schedule", "extraction": which recipe's defaults apply
    query: str = ""
    tags: tuple[str, ...] = ()
    extra: dict[str, Any] = None     # source-specific inputs, e.g. {"session_text": ...}

    def __post_init__(self) -> None:
        if self.extra is None:
            self.extra = {}
