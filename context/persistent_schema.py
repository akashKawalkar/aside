# context/persistent_schema.py — the shape of the persistent file: five sections of dated, sourced entries.
# Spec and rationale: docs/persistent_file.md. M3a stores this in the database; the context source reads it from here.
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

Source = Literal["user", "agent", "note", "pattern"]
Status = Literal["active", "provisional", "retired"]

# name -> (always in context, what belongs there)
SECTIONS: dict[str, tuple[bool, str]] = {
    "identity": (True, "Identity and stable facts: who you are, where you live, what you do."),
    "preferences": (True, "Hard preferences and constraints: how things must be done, what must never happen."),
    "routine": (True, "Routine skeleton: the usual weekday and weekend shape of the day."),
    "goals": (False, "Goals and current focus: what you are working toward right now."),
    "people": (False, "People: who matters, and what the assistant should know about them."),
}
ALWAYS_ON = tuple(name for name, (always, _) in SECTIONS.items() if always)
LIVE_STATUSES = ("active", "provisional")   # retired entries stay in the log but never reach a prompt


class Entry(BaseModel):
    """One fact. These six fields and nothing else."""
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500)
    source: Source = "user"
    created: datetime
    last_confirmed: datetime | None = None
    evidence_count: int = Field(default=1, ge=1)
    status: Status = "active"

    @field_validator("text")
    @classmethod
    def _tidy(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("text must not be blank")
        return value

    @field_validator("created", "last_confirmed")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must carry a UTC offset")
        return value

    @model_validator(mode="after")
    def _order(self) -> Entry:
        if self.last_confirmed is not None and self.last_confirmed < self.created:
            raise ValueError("last_confirmed cannot be before created")
        return self


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[Entry] = Field(default_factory=list)

    @field_validator("entries")
    @classmethod
    def _no_duplicates(cls, entries: list[Entry]) -> list[Entry]:
        seen: set[str] = set()
        for entry in entries:
            if entry.status == "retired":
                continue
            key = entry.text.casefold()
            if key in seen:
                raise ValueError(f"duplicate entry: {entry.text!r}")
            seen.add(key)
        return entries


class PersistentFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = SCHEMA_VERSION
    sections: dict[str, Section] = Field(default_factory=dict)

    @field_validator("sections")
    @classmethod
    def _known_sections(cls, sections: dict[str, Section]) -> dict[str, Section]:
        unknown = sorted(set(sections) - set(SECTIONS))
        if unknown:
            raise ValueError(f"unknown sections {unknown}; the sections are {list(SECTIONS)}")
        # Every section is always present, so readers never need a default.
        return {name: sections.get(name, Section()) for name in SECTIONS}

    def live(self, section: str) -> list[Entry]:
        """The entries that may reach a prompt: not retired."""
        return [e for e in self.sections[section].entries if e.status in LIVE_STATUSES]


def load_persistent_file(path: str | Path) -> PersistentFile:
    return PersistentFile.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def empty_file() -> PersistentFile:
    return PersistentFile(sections={})
