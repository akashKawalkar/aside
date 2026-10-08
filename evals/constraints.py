"""Code-checkable response constraints used by the eval suite."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Callable


@dataclass(frozen=True)
class Check:
    passed: bool
    detail: str


@dataclass(frozen=True)
class Constraint:
    id: str
    description: str
    check: Callable[[str], Check]


def max_words(limit: int) -> Constraint:
    if limit < 1:
        raise ValueError("word limit must be positive")

    def check(text: str) -> Check:
        count = len(text.split())
        return Check(count <= limit, f"{count} words (limit {limit})")

    return Constraint(f"max_words_{limit}", f"Use at most {limit} words.", check)


def no_emoji() -> Constraint:
    def check(text: str) -> Check:
        found = any(_is_emoji_char(char) for char in text)
        return Check(not found, "emoji found" if found else "no emoji found")

    return Constraint("no_emoji", "Do not use emoji.", check)


def contains(phrase: str) -> Constraint:
    def check(text: str) -> Check:
        found = phrase.casefold() in text.casefold()
        return Check(found, f"required phrase {'present' if found else 'missing'}: {phrase!r}")

    return Constraint(f"contains:{phrase.casefold()}", f"Include {phrase!r}.", check)


def excludes(phrase: str) -> Constraint:
    def check(text: str) -> Check:
        found = phrase.casefold() in text.casefold()
        return Check(not found, f"forbidden phrase {'found' if found else 'absent'}: {phrase!r}")

    return Constraint(f"excludes:{phrase.casefold()}", f"Do not include {phrase!r}.", check)


def _is_emoji_char(char: str) -> bool:
    code = ord(char)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x1F1E6 <= code <= 0x1F1FF
        or unicodedata.category(char) == "So"
        or code in (0xFE0F, 0x20E3, 0x200D)
    )


CONSTRAINTS = {
    "max_words_20": max_words(20),
    "max_words_50": max_words(50),
    "no_emoji": no_emoji(),
    "vegetarian": contains("vegetarian"),
    "no_meat": excludes("chicken"),
}
