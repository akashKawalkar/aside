# capture/monitoring.py
"""
The monitoring on/off switch, shared between processes through one small file.

The server writes it (when you pause from the panel, settings or a shortcut),
and the collector reads it, so pausing works even when Postgres or the server
is down. A missing file means "recording".
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class MonitoringFlag:
    def __init__(self, path: str | Path, *, ttl: float = 2.0, clock=time.monotonic) -> None:
        self.path = Path(path)
        self.ttl = ttl
        self._clock = clock
        self._value = True
        self._checked_at: float | None = None

    def enabled(self) -> bool:
        """Current state. Re-reads the file at most once per `ttl` seconds."""
        now = self._clock()

        if self._checked_at is None or now - self._checked_at >= self.ttl:
            self._value = self._read()
            self._checked_at = now

        return self._value

    def set(self, enabled: bool) -> None:
        """Write the state atomically so a reader never sees a half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")

        tmp.write_text(
            json.dumps(
                {
                    "enabled": bool(enabled),
                    "changed_at": datetime.now(timezone.utc).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

        self._value = bool(enabled)
        self._checked_at = self._clock()

    def _read(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return bool(data["enabled"])
        except FileNotFoundError:
            return True
        except (OSError, ValueError, KeyError, TypeError):
            # An unreadable file keeps the last known state instead of
            # silently flipping it.
            log.warning("monitoring state file unreadable, keeping %s", self._value)
            return self._value
