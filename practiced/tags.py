"""Expected categories of calendar titles: one cached model call per new title (planning_design.md 2.3).

The model only says what a title should look like on the devices; code validates the answer against the category list and
caches it per title. Tags can be edited by the user (origin 'user' is never overwritten).
"""
from __future__ import annotations

import json
from typing import Any

from digital.rules import CATEGORIES
from llm.client import LLMClient, Message
from practiced.builder import Tag
from practiced.llmcall import call_json

ALLOWED = tuple(c for c in CATEGORIES if c not in ("system", "idle"))
MAX_TOKENS = 1500

SYSTEM = f"""You label calendar blocks for a personal time tracker.
For each calendar title, say which device activity categories would show up on a laptop or phone if the person really did
that block, and whether the devices can see it at all.

Categories: {", ".join(ALLOWED)}.

Rules:
- "expected": the categories that count as doing the block. Usually one to three. Do not include entertainment categories
  (media, social, shopping) unless the title itself is about them.
- "observable": false when the activity happens away from the screens (exercise, meditation, a meal, a walk, sleep, travel,
  prayer, chores). Then "expected" is an empty list or ["wellbeing"] if a wellbeing app could plausibly be open.
- Reason from the title only. Do not invent details.

Answer with JSON only: {{"tags": [{{"title": "<exact title>", "reason": "<one short sentence>", "expected": [...], "observable": true}}]}}
Put "reason" before "expected". Return one entry per title, with the title copied exactly.

Example input:
["Deep work: API refactor", "Morning Workout", "Job applications", "Dinner with family"]

Example output:
{json.dumps({"tags": [
    {"title": "Deep work: API refactor", "reason": "writing code", "expected": ["coding", "ai_assist"], "observable": True},
    {"title": "Morning Workout", "reason": "exercise away from the screens", "expected": [], "observable": False},
    {"title": "Job applications", "reason": "applying on portals and writing mail", "expected": ["job_search", "comms", "work_admin"], "observable": True},
    {"title": "Dinner with family", "reason": "a meal, not on a screen", "expected": [], "observable": False},
]})}"""


def parse_tags(data: Any, titles: list[str]) -> dict[str, Tag]:
    """Only entries for titles that were asked about, with only known categories; anything else is dropped."""
    out: dict[str, Tag] = {}
    items = data.get("tags") if isinstance(data, dict) else None
    for item in items or []:
        if not isinstance(item, dict) or item.get("title") not in titles:
            continue
        cats = tuple(c for c in item.get("expected") or [] if c in ALLOWED)
        out[item["title"]] = Tag(expected=cats, observable=bool(item.get("observable", True)))
    return out


async def request_tags(client: LLMClient, titles: list[str]) -> dict[str, Tag]:
    parsed, _ = await call_json(
        client, [Message("system", SYSTEM), Message("user", json.dumps(titles))], max_tokens=MAX_TOKENS,
    )
    return parse_tags(parsed, titles)
