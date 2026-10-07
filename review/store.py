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


class ReviewStore:
    """
    The review settings and the latest review, as two small JSON files in
    the data directory. Only the newest review is kept.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.settings_path = self.directory / "review_settings.json"
        self.latest_path = self.directory / "review_latest.json"

    def settings(self) -> ReviewSettings:
        data = self._read(self.settings_path)
        fields = ReviewSettings.__dataclass_fields__

        try:
            return ReviewSettings(
                **{k: v for k, v in data.items() if k in fields}
            ).validated()
        except (ValueError, TypeError):
            return ReviewSettings()

    def save_settings(self, settings: ReviewSettings) -> ReviewSettings:
        settings = settings.validated()
        self._write(self.settings_path, asdict(settings))

        return settings

    def latest(self) -> dict[str, Any] | None:
        return self._read(self.latest_path) or None

    def save_latest(self, review) -> None:
        self._write(
            self.latest_path,
            {"date": review.review_date.isoformat(), "data": review.data, "text": review.text},
        )

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

        return value if isinstance(value, dict) else {}

    def _write(self, path: Path, value: dict[str, Any]) -> None:
        """Atomic, so a reader never sees a half-written file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(value), encoding="utf-8")
        os.replace(tmp, path)
