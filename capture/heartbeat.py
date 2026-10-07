# capture/heartbeat.py — "I am running", as a small file each process touches. A file (not the database) so a
# stopped collector is visible even when Postgres or the server is down; the server samples these into heartbeat_log.
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class Heartbeat:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def beat(self, now: datetime | None = None) -> None:
        """Atomic, so a reader never sees a half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": (now or datetime.now(timezone.utc)).isoformat(), "pid": os.getpid()}), encoding="utf-8")
        os.replace(tmp, self.path)

    def age(self, now: datetime | None = None) -> float | None:
        """Seconds since the last beat, or None if there has never been one (or the file is unreadable)."""
        try:
            ts = datetime.fromisoformat(json.loads(self.path.read_text(encoding="utf-8"))["ts"])
        except (OSError, ValueError, KeyError):
            return None
        return max(((now or datetime.now(timezone.utc)) - ts).total_seconds(), 0.0)

    def alive(self, max_age: float, now: datetime | None = None) -> bool:
        age = self.age(now)
        return age is not None and age <= max_age


async def run_heartbeat(heartbeat: Heartbeat, interval: float) -> None:
    while True:
        try:
            heartbeat.beat()
        except OSError:
            pass   # never let a full disk or a locked file stop the process it reports on
        await asyncio.sleep(interval)


def heartbeat_files(directory: str | Path) -> tuple[Heartbeat, Heartbeat]:
    directory = Path(directory)
    return Heartbeat(directory / "heartbeat_collector.json"), Heartbeat(directory / "heartbeat_ingestor.json")
