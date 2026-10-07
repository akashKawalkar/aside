# llm/quota.py — which calls count as "today". The provider's daily quota (RPD) resets at midnight PACIFIC time (Google's
# rate-limit docs), not at midnight IST, so the cap is counted from the provider's own day boundary: 12:30 IST during US
# daylight time, 13:30 IST otherwise. Counting from IST midnight would let calls made just before the real reset
# count against the wrong day and hide how much allowance is actually left.
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

PROVIDER_TZ = ZoneInfo("America/Los_Angeles")


def provider_day_start(now: datetime) -> datetime:
    """The most recent midnight in Pacific time at or before `now` (timezone-aware)."""
    local = now.astimezone(PROVIDER_TZ)
    return datetime.combine(local.date(), time.min, PROVIDER_TZ)
