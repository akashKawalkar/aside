from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiffEntry:
    """
    One complete persistent-file change.

    before:
        Exact state before the change.

    after:
        Exact state after the change.

    removed:
        Exact content removed by the change.

    added:
        Exact content added by the change.

    source_type/source_id:
        What caused the change, e.g. note, correction, or session.
    """

    id: int
    created_at: datetime
    source_type: str
    source_id: str | int | None
    before: dict[str, Any]
    after: dict[str, Any]
    removed: dict[str, Any]
    added: dict[str, Any]


class DiffLogger:
    """
    Durable append-only logger for persistent-file changes.

    No model calls.
    No compiler logic.
    No promotion logic.
    No Postgres dependency.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log_change(
        self,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        source_type: str,
        source_id: str | int | None = None,
    ) -> DiffEntry | None:
        """
        Compute and durably record one persistent-file change.

        Returns the created entry on success.

        Returns None on failure so a logging failure does not crash
        the rest of the program.
        """

        try:
            source_type = source_type.strip()

            if not source_type:
                return None

            removed, added = self._diff(
                before=before,
                after=after,
            )

            # No actual change -> no log entry.
            if not removed and not added:
                return None

            entry = DiffEntry(
                id=self._next_id(),
                created_at=datetime.now(timezone.utc),
                source_type=source_type,
                source_id=source_id,
                before=self._copy_dict(before),
                after=self._copy_dict(after),
                removed=removed,
                added=added,
            )

            self._append(entry)

            return entry

        except Exception:
            return None

    def read_all(self) -> list[DiffEntry]:
        """
        Read all recorded diff entries.

        Corrupt individual lines are skipped so one damaged record
        does not make the entire log unreadable.
        """

        try:
            if not self.path.exists():
                return []

            entries: list[DiffEntry] = []

            for line in self.path.read_text(
                encoding="utf-8"
            ).splitlines():

                if not line.strip():
                    continue

                try:
                    data = json.loads(line)

                    entries.append(
                        DiffEntry(
                            id=int(data["id"]),
                            created_at=datetime.fromisoformat(
                                data["created_at"]
                            ),
                            source_type=str(
                                data["source_type"]
                            ),
                            source_id=data.get("source_id"),
                            before=dict(data.get("before", {})),
                            after=dict(data.get("after", {})),
                            removed=dict(data.get("removed", {})),
                            added=dict(data.get("added", {})),
                        )
                    )

                except Exception:
                    continue

            return entries

        except Exception:
            return []

    def get(
        self,
        entry_id: int,
    ) -> DiffEntry | None:
        """
        Return one diff entry by ID.
        """

        for entry in self.read_all():
            if entry.id == entry_id:
                return entry

        return None

    def _append(
        self,
        entry: DiffEntry,
    ) -> None:
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {
            "id": entry.id,
            "created_at": entry.created_at.isoformat(),
            "source_type": entry.source_type,
            "source_id": entry.source_id,
            "before": entry.before,
            "after": entry.after,
            "removed": entry.removed,
            "added": entry.added,
        }

        line = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"

        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )

        try:
            # Append using a temporary file containing the complete
            # record, then append its bytes to the log.
            with os.fdopen(
                fd,
                "wb",
            ) as temporary:
                temporary.write(line.encode("utf-8"))
                temporary.flush()
                os.fsync(temporary.fileno())

            with self.path.open(
                "ab",
            ) as log_file:
                log_file.write(
                    Path(temporary_path).read_bytes()
                )
                log_file.flush()
                os.fsync(log_file.fileno())

        finally:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass

    def _next_id(self) -> int:
        entries = self.read_all()

        if not entries:
            return 1

        return max(entry.id for entry in entries) + 1

    @classmethod
    def _diff(
        cls,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Return exact changed/removed values and exact added/changed values.

        Nested dictionaries are represented recursively.
        """

        removed: dict[str, Any] = {}
        added: dict[str, Any] = {}

        keys = set(before) | set(after)

        for key in sorted(keys):
            in_before = key in before
            in_after = key in after

            if in_before and not in_after:
                removed[key] = cls._copy_value(before[key])
                continue

            if in_after and not in_before:
                added[key] = cls._copy_value(after[key])
                continue

            old_value = before[key]
            new_value = after[key]

            if old_value == new_value:
                continue

            if (
                isinstance(old_value, dict)
                and isinstance(new_value, dict)
            ):
                nested_removed, nested_added = cls._diff(
                    before=old_value,
                    after=new_value,
                )

                if nested_removed:
                    removed[key] = nested_removed

                if nested_added:
                    added[key] = nested_added

            else:
                removed[key] = cls._copy_value(old_value)
                added[key] = cls._copy_value(new_value)

        return removed, added

    @staticmethod
    def _copy_dict(
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            key: DiffLogger._copy_value(item)
            for key, item in value.items()
        }

    @staticmethod
    def _copy_value(value: Any) -> Any:
        """
        Create a JSON-safe detached copy.
        """

        try:
            return json.loads(
                json.dumps(
                    value,
                    ensure_ascii=False,
                )
            )
        except Exception:
            return repr(value)