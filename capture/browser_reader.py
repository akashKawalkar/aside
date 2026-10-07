# capture/browser_reader.py
"""
Browser activity reported by the extension: which site was in the focused
browser tab for a stretch of time. Stored as ordinary events with
source="browser" so the app timeline (source="laptop") is never double counted.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from capture.privacy import redact_title_for_domain
from storage import Event

BROWSER_SOURCE = "browser"
MAX_TITLE_CHARS = 300
# The extension reports about one segment a minute. Anything much longer means
# its service worker slept through a gap, which must not be recorded as activity.
MAX_SEGMENT = timedelta(minutes=15)

_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


class BrowserEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=2000)
    ts_start: datetime
    ts_end: datetime

    @field_validator("domain")
    @classmethod
    def _clean_domain(cls, value: str) -> str:
        domain = value.strip().strip(".").casefold()
        if not _HOSTNAME.match(domain):
            raise ValueError("domain must be a bare host name, not a URL")
        return domain

    @field_validator("ts_start", "ts_end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "BrowserEvent":
        if self.ts_end < self.ts_start:
            raise ValueError("ts_end must not be before ts_start")
        if self.ts_end - self.ts_start > MAX_SEGMENT:
            raise ValueError("segment is too long to be one stretch of activity")
        return self


def to_event(item: BrowserEvent, extra_sensitive_domains: Iterable[str] = ()) -> Event:
    """Apply the title privacy rules and build the event to store."""
    title = redact_title_for_domain(item.domain, item.title, extra_sensitive_domains)
    if title is not None:
        title = title.strip()[:MAX_TITLE_CHARS] or None

    return Event(
        source=BROWSER_SOURCE,
        kind="app_focus",
        app=None,
        window_title=title,
        domain=item.domain,
        ts_start=item.ts_start,
        ts_end=item.ts_end,
    )
