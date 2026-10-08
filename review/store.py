from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FREQUENCIES = ("daily", "weekly", "monthly")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class ReviewSettings:
    """
    email_enabled stays off until you choose otherwise; the review itself
    is always written, and only ever shows up when you open settings.
    """

    email_enabled: bool = False
    frequency: str = "daily"
    recipient: str = ""
    # Local time the nightly review is written, "HH:MM".
    time: str = "21:30"

    def validated(self) -> "ReviewSettings":
        if self.frequency not in FREQUENCIES:
            raise ValueError(f"frequency must be one of {', '.join(FREQUENCIES)}")

        if not _TIME_RE.match(self.time):
            raise ValueError("time must look like 21:30")

        recipient = self.recipient.strip()
        if recipient and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", recipient):
            raise ValueError("recipient is not a valid email address")

        if self.email_enabled and not recipient:
            raise ValueError("add an email address before turning email on")

        return ReviewSettings(
            email_enabled=bool(self.email_enabled),
            frequency=self.frequency,
            recipient=recipient,
            time=self.time,
        )


class FileBackend:
    """Two small JSON files in a directory (tests, and the pre-cloud layout)."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / f"review_{key}.json"

    async def get(self, key: str) -> dict[str, Any]:
        try:
            value = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

        return value if isinstance(value, dict) else {}

    async def put(self, key: str, value: dict[str, Any]) -> None:
        """Atomic, so a reader never sees a half-written file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)


class DbBackend:
    """The `review_state` table, so the laptop and the nightly job share one copy.
    `pool` may be a pool or a zero-argument callable returning one (the server opens its pool late)."""

    def __init__(self, pool) -> None:
        self._pool = pool

    def _get_pool(self):
        return self._pool() if callable(self._pool) else self._pool

    async def get(self, key: str) -> dict[str, Any]:
        from storage import get_review_state

        return await get_review_state(self._get_pool(), key) or {}

    async def put(self, key: str, value: dict[str, Any]) -> None:
        from storage import set_review_state

        await set_review_state(self._get_pool(), key, value)


class ReviewStore:
    """
    The review settings and the latest review (only the newest is kept),
    as two JSON values in a backend: a directory path (files) or a backend object.
    """

    def __init__(self, backend: Any) -> None:
        self.backend = backend if hasattr(backend, "get") else FileBackend(backend)

    async def settings(self) -> ReviewSettings:
        data = await self.backend.get("settings")
        fields = ReviewSettings.__dataclass_fields__

        try:
            return ReviewSettings(
                **{k: v for k, v in data.items() if k in fields}
            ).validated()
        except (ValueError, TypeError):
            return ReviewSettings()

    async def save_settings(self, settings: ReviewSettings) -> ReviewSettings:
        settings = settings.validated()
        await self.backend.put("settings", asdict(settings))

        return settings

    async def latest(self) -> dict[str, Any] | None:
        return await self.backend.get("latest") or None

    async def save_latest(self, review) -> None:
        await self.backend.put(
            "latest",
            {"date": review.review_date.isoformat(), "data": review.data, "text": review.text},
        )

    async def import_legacy_files(self, directory: str | Path) -> list[str]:
        """One-time move of the old data/review_*.json files into this store (skips keys already present)."""
        files, moved = FileBackend(directory), []
        for key in ("settings", "latest"):
            if await self.backend.get(key):
                continue
            value = await files.get(key)
            if value:
                await self.backend.put(key, value)
                moved.append(key)

        return moved
