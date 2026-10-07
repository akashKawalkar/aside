# llm/tokens.py — a provider-free token estimate: characters / ratio, inflated by a safety margin.
from __future__ import annotations

import math

from llm.client import Message, ModelProfile

DEFAULT_CHARS_PER_TOKEN = 4.0
DEFAULT_SAFETY_MARGIN = 1.15
PER_MESSAGE_OVERHEAD = 4


def estimate_tokens(text: str, profile: ModelProfile | None = None) -> int:
    """Deliberately a little high, so a packed context never overflows the window."""
    if not text:
        return 0
    ratio = profile.chars_per_token if profile else DEFAULT_CHARS_PER_TOKEN
    margin = profile.safety_margin if profile else DEFAULT_SAFETY_MARGIN
    return math.ceil(len(text) / ratio * margin)


def estimate_messages(messages: list[Message], profile: ModelProfile | None = None) -> int:
    return sum(estimate_tokens(m.content, profile) + PER_MESSAGE_OVERHEAD for m in messages)
