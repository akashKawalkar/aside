# storage/repo/event.py
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
import json
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from pydantic import ValidationError

INSERT_SQL = """
    INSERT INTO events
        (source, kind, app, window_title, domain, ts_start, ts_end, payload, dedupe_key)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (dedupe_key) DO NOTHING
"""

class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    kind: Literal["app_focus", "idle"]
    app: str | None = None
    window_title: str | None = None
    domain: str | None = None
    ts_start: datetime
    ts_end: datetime
    payload: dict[str, Any] = {}

    @field_validator("ts_start", "ts_end")
    def require_utc_datetime(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")

        return value.astimezone(timezone.utc)

    @property
    def dedupe_key(self) -> str:
        raw = "|".join([
            self.source,
            self.kind,
            self.ts_start.isoformat(),
            self.app or "",
            self.window_title or "",
            self.domain or ""
        ])

        return sha256(raw.encode("utf-8")).hexdigest()
    # @field_validator("app", "window_title", "domain")
    # @classmethod
    # def strip_nul(cls, value: str | None) -> str | None:
    #     # Postgres rejects NUL characters in text, which would fail a whole file
    #     return value.replace("\x00", "") if value else value

    # @model_validator(mode="after")
    # def check_order(self) -> "Event":
    #     if self.ts_end < self.ts_start:
    #         raise ValueError("ts_end must not be before ts_start")
    #     return self

def _row_for(e: "Event") -> tuple:
    return (
        e.source,
        e.kind,
        e.app,
        e.window_title,
        e.domain,
        e.ts_start,
        e.ts_end,
        Jsonb(e.payload),
        e.dedupe_key,
    )


async def insert_many(pool, events: list["Event"]) -> int:
    """Insert events, ignoring duplicates. Returns the number of rows sent.

    Does not tell you how many actually landed (ON CONFLICT DO NOTHING
    hides that); the caller logs the sent count.
    """
    if not events:
        return 0

    rows = [_row_for(e) for e in events]

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(INSERT_SQL, rows)

    return len(rows)


def parse_spool_file(path: Path) -> tuple[list["Event"], list[str]]:
    """Parse one spool file into (events, bad_lines).

    bad_lines are JSON strings with {line, raw, error} for the dead/ file.
    Lives here so the ingestor doesn't need to know the Event schema.
    """
    events: list[Event] = []
    bad: list[str] = []

    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(Event.model_validate_json(line))
        except ValidationError as exc:
            bad.append(json.dumps({"line": n, "raw": line, "error": str(exc)}))

    return events, bad
def dedupe_key_for(event: Event) -> str:
    return event.dedupe_key