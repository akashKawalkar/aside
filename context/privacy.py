# context/privacy.py — the privacy layer: what may never leave the machine (plan §3.9). One implementation, used by
# context compiles and by background jobs that read the user's text. Config: [privacy] deny_sources / deny_tags.
from __future__ import annotations

from config import Privacy, load_config
from context.items import Item


def denied_reason(source: str, tags: tuple[str, ...] | list[str], rules: Privacy) -> str | None:
    """Why something from `source` carrying `tags` must not be sent to a model, or None if it may go."""
    if source in rules.deny_sources:
        return "privacy_denied_source"
    if set(tags or ()) & set(rules.deny_tags):
        return "privacy_denied_tag"
    return None


def apply_privacy_filter(items: list[Item], rules: Privacy | None = None) -> tuple[list[Item], list[tuple[Item, str]]]:
    """(allowed, [(denied item, reason)])."""
    rules = rules or load_config().privacy
    allowed, denied = [], []
    for item in items:
        reason = denied_reason(item.source, item.tags, rules)
        if reason:
            denied.append((item, reason))
        else:
            allowed.append(item)
    return allowed, denied
