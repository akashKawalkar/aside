"""Category rules: which category and label an app, website or window title gets.

A rule matches when every condition it has is true (case-insensitive substrings):
  source      'laptop', 'phone' or None (any)
  app         the exe name (laptop) or package name (phone)
  domain      the website, from the browser extension
  title_has   any one of these words is in the window title

Rules are tried highest priority first (ties: the lowest id). No match gives category `other`.
Pure code, no database: the table in the database only stores the rules (see digital/store.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CATEGORIES = (
    "coding", "study", "ai_assist", "work_admin", "comms", "social", "media", "job_search",
    "networking", "wellbeing", "shopping", "other", "system", "idle",
)

# Categories that count as doing the planned kind of work when no calendar tag says otherwise.
PRODUCTIVE = frozenset({"coding", "study", "work_admin", "job_search", "networking"})


@dataclass(frozen=True)
class Rule:
    category: str
    label: str
    app: str | None = None
    domain: str | None = None
    title_has: tuple[str, ...] = ()
    source: str | None = None
    priority: int = 50
    use_title: bool = True
    note: str = ""          # why the rule exists; a guess is marked "REVIEW"
    id: int = 0
    enabled: bool = True


@dataclass(frozen=True)
class Match:
    category: str
    label: str
    rule_id: int


def matches(rule: Rule, source: str, app: str | None, domain: str | None, title: str | None) -> bool:
    if not rule.enabled:
        return False
    if rule.source and rule.source != source:
        return False
    if rule.app and rule.app.lower() not in (app or "").lower():
        return False
    if rule.domain and rule.domain.lower() not in (domain or "").lower():
        return False
    if rule.title_has:
        t = (title or "").lower()
        if not any(k.lower() in t for k in rule.title_has):
            return False
    return True


def ordered(rules: list[Rule]) -> list[Rule]:
    return sorted(rules, key=lambda r: (-r.priority, r.id))


_COUNT = re.compile(r"^\(\d+\)\s*")
_SUFFIXES = (" - Google Chrome", " - YouTube", " | LinkedIn", " - ChatGPT", " - Claude", " - Notion", " - Gmail")


def clean_title(title: str | None, limit: int = 48) -> str:
    """A window title without the notification count and the site name at the end."""
    t = _COUNT.sub("", (title or "").strip())
    for suffix in _SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    if t.strip().lower() in {"youtube", "linkedin", "chatgpt", "claude", "google chrome", "new tab"}:
        return ""
    return t.strip()[:limit]


def make_label(rule: Rule, app: str | None, domain: str | None, title: str | None) -> str:
    if rule.label == "VS Code":
        t = (title or "").lstrip("● ").strip()
        parts = [p.strip() for p in t.split(" - ")]
        if parts and parts[-1].lower() == "visual studio code":
            parts = parts[:-1]
        if len(parts) >= 2:
            return f"VS Code: {parts[-1]} ({parts[0][:30]})"
        return f"VS Code: {parts[0][:40]}" if parts and parts[0] else "VS Code"
    if not rule.use_title:
        return rule.label
    cleaned = clean_title(title)
    if cleaned:
        return f"{rule.label}: {cleaned}"
    if "youtube" in (domain or "").lower():
        return f"{rule.label}: home/feed"
    return rule.label


def classify(rules: list[Rule], source: str, app: str | None, domain: str | None, title: str | None) -> Match:
    """`rules` must already be in `ordered()` order (the builder does that once, not per fragment)."""
    for r in rules:
        if matches(r, source, app, domain, title):
            return Match(r.category, make_label(r, app, domain, title), r.id)
    base = (domain or app or "unknown").strip()
    return Match("other", base, 0)
