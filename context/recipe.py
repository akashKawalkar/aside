# context/recipe.py — a recipe is plain config: which sources, in what priority, within what budget.
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import ROOT

RECIPES_PATH = ROOT / "config" / "recipes.toml"
DEFAULT_EXCLUDED = ("retired", "shelved")


@dataclass(frozen=True)
class SourceSpec:
    name: str
    priority: int
    cap_fraction: float = 1.0
    max_items: int | None = None
    min_confidence: float = 0.0
    exclude_status: tuple[str, ...] = DEFAULT_EXCLUDED


@dataclass(frozen=True)
class Recipe:
    name: str
    input_fraction: float
    selector: str = "off"            # off | rules | llm
    sources: tuple[SourceSpec, ...] = field(default_factory=tuple)
    max_input_tokens: int | None = None   # absolute ceiling; a fraction alone means nothing against a 1M-token window

    def spec(self, source: str) -> SourceSpec | None:
        return next((s for s in self.sources if s.name == source), None)

    def budget(self, window: int) -> int:
        """Tokens the packed context may use: a share of the model window, never more than the ceiling."""
        by_fraction = int(window * self.input_fraction)
        return by_fraction if self.max_input_tokens is None else min(by_fraction, self.max_input_tokens)

    def ordered(self) -> list[SourceSpec]:
        return sorted(self.sources, key=lambda s: -s.priority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "input_fraction": self.input_fraction, "selector": self.selector,
            "max_input_tokens": self.max_input_tokens,
            "sources": [
                {"name": s.name, "priority": s.priority, "cap_fraction": s.cap_fraction, "max_items": s.max_items,
                 "min_confidence": s.min_confidence, "exclude_status": list(s.exclude_status)}
                for s in self.sources
            ],
        }

    @property
    def hash(self) -> str:
        """Short digest of the settings, so a compile log says which exact recipe produced it."""
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:12]


def _recipe(name: str, raw: dict[str, Any]) -> Recipe:
    sources = tuple(
        SourceSpec(
            name=source,
            priority=int(opts["priority"]),
            cap_fraction=float(opts.get("cap_fraction", 1.0)),
            max_items=opts.get("max_items"),
            min_confidence=float(opts.get("min_confidence", 0.0)),
            exclude_status=tuple(opts.get("exclude_status", DEFAULT_EXCLUDED)),
        )
        for source, opts in raw.get("sources", {}).items()
    )
    ceiling = raw.get("max_input_tokens")
    return Recipe(
        name=name, input_fraction=float(raw["input_fraction"]), selector=raw.get("selector", "off"), sources=sources,
        max_input_tokens=None if ceiling is None else int(ceiling),
    )


def load_recipes(path: Path | None = None) -> dict[str, Recipe]:
    with open(path or RECIPES_PATH, "rb") as f:
        return {name: _recipe(name, raw) for name, raw in tomllib.load(f).items()}


def load_recipe(name: str, path: Path | None = None) -> Recipe:
    recipes = load_recipes(path)
    if name not in recipes:
        raise ValueError(f"no recipe named {name!r}; have {sorted(recipes)}")
    return recipes[name]
