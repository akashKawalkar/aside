# context/persistent_rules.py — the persistent file's memory rules as pure functions over entry dicts.
# Spec: docs/persistent_file.md and ASIDE_PLAN.md §3.3. Storage and logging live in storage/repo/persistent_entries.py.
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from context.persistent_schema import LIVE_STATUSES

IST = timezone(timedelta(hours=5, minutes=30))


def ist_day(now: datetime) -> date:
    return now.astimezone(IST).date()


def with_day(seen_days: list[date], now: datetime) -> list[date]:
    """`seen_days` plus today (IST), sorted, without repeats. A fact seen twice in a day counts once."""
    return sorted({*seen_days, ist_day(now)})


def is_settled(seen_days: list[date], settle_days: int) -> bool:
    """Seen on enough separate days to stop being provisional."""
    return len(set(seen_days)) >= settle_days


def eviction_order(entries: list[dict], evict_first: list[str]) -> list[dict]:
    """Live entries, first to go first: dynamic sections, then least evidence, then longest unconfirmed."""
    live = [e for e in entries if e["status"] in LIVE_STATUSES]
    return sorted(live, key=lambda e: (e["section"] not in evict_first, e["evidence_count"], e["last_confirmed"] or e["created"]))


def pick_evictions(entries: list[dict], cap_tokens: int, evict_first: list[str], estimate: Callable[[str], int],
                   keep_ids: frozenset[int] = frozenset()) -> list[dict]:
    """The entries to retire so the live ones fit `cap_tokens`. Entries in `keep_ids` (just written) are never picked."""
    total = sum(estimate(e["text"]) for e in entries if e["status"] in LIVE_STATUSES)
    picked: list[dict] = []
    for e in eviction_order(entries, evict_first):
        if total <= cap_tokens:
            break
        if e["id"] in keep_ids:
            continue
        picked.append(e)
        total -= estimate(e["text"])
    return picked
