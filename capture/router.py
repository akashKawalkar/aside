# capture/router.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


Destination = Literal[
    "note",
    "journal_note",
    "task",
    "schedule",
    "chat_llm",
    "chat_script",
    "wrong",
    "find",
]

Mode = Literal[
    "note",
    "task",
    "schedule",
    "chat",
]

Key = Literal[
    "enter",
    "ctrl_enter",
]


@dataclass(frozen=True)
class RouteResult:
    destination: Destination | None
    text: str
    reason: str | None
    error: str | None


TRIGGERS = {
    "note:": "note",
    "journal:": "journal_note",
    "task:": "task",
    "schedule:": "schedule",
    "find:": "find",     # looks notes up locally (keyword + local embeddings); no model call, nothing saved
    "wrong:": "wrong",   # marks the previous reply wrong; the text after it is an optional reason
}


def classify(text: str) -> str:
    return "note"


def route(
    text: str,
    mode: str,
    key: str,
) -> RouteResult:
    text = text.lstrip()

    if not text:
        return RouteResult(
            destination=None,
            text="",
            reason=None,
            error="Input must not be empty.",
        )

    if text.startswith("\\"):
        text = text[1:]

        if not text:
            return RouteResult(
                destination=None,
                text="",
                reason=None,
                error="Input must not be empty.",
            )

        return _route_without_triggers(text, mode, key)

    lowered = text.lower()

    for prefix, destination in TRIGGERS.items():
        if lowered.startswith(prefix):
            content = text[len(prefix):].lstrip()

            if destination == "note" and not content:
                return RouteResult(
                    destination=None,
                    text="",
                    reason="trigger",
                    error="Note text must not be empty.",
                )

            if destination == "find" and not content:
                return RouteResult(destination=None, text="", reason="trigger", error="Say what to look for after find:.")

            return RouteResult(
                destination=destination,
                text=content,
                reason="trigger",
                error=None,
            )

    return _route_without_triggers(text, mode, key)


def _route_without_triggers(
    text: str,
    mode: str,
    key: str,
) -> RouteResult:
    if mode in {"task", "schedule", "note"}:
        return RouteResult(
            destination=mode,
            text=text,
            reason="mode",
            error=None,
        )

    if mode == "chat":
        if key == "enter":
            return RouteResult(
                destination="chat_llm",
                text=text,
                reason="key",
                error=None,
            )

        if key == "ctrl_enter":
            return RouteResult(
                destination="chat_script",
                text=text,
                reason="key",
                error=None,
            )

        return RouteResult(
            destination=None,
            text=text,
            reason=None,
            error=f"Unknown key: {key}",
        )

    return RouteResult(
        destination=None,
        text=text,
        reason=None,
        error=f"Unknown mode: {mode}",
    )
