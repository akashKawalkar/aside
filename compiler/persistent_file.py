from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PersistentFile:
    """
    The current persistent context.

    Static:
        Stable information that is not directly edited by normal writes.

    Dynamic:
        Instructions, preferences, corrections, and the note-topic index.
    """

    static: dict[str, Any] = field(default_factory=dict)
    dynamic: dict[str, Any] = field(default_factory=dict)


class PersistentFileStore:
    """
    Read and write the persistent context file.

    This component owns only the current persistent state.

    It does not:
        - compile context
        - perform promotion
        - log diffs
        - call models
        - access Postgres
    """

    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> PersistentFile:
        """
        Load the current persistent file.

        A missing or unreadable file safely becomes an empty file.
        The compiler must be able to continue with no persistent data.
        """

        try:
            if not self.path.exists():
                return PersistentFile()

            data = json.loads(
                self.path.read_text(encoding="utf-8")
            )

            return self._from_dict(data)

        except Exception:
            return PersistentFile()

    def save(self, persistent: PersistentFile) -> bool:
        """
        Atomically save the complete persistent state.

        Returns False rather than crashing if persistence fails.
        """

        try:
            self.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            data = self._to_dict(persistent)

            fd, temporary_path = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )

            try:
                with os.fdopen(
                    fd,
                    "w",
                    encoding="utf-8",
                ) as file:
                    json.dump(
                        data,
                        file,
                        ensure_ascii=False,
                        indent=2,
                    )
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())

                os.replace(
                    temporary_path,
                    self.path,
                )

            except Exception:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

                raise

            return True

        except Exception:
            return False

    # def get_static(
    #     self,
    #     key: str,
    #     default: Any = None,
    # ) -> Any:
    #     persistent = self.load()
    #     return persistent.static.get(key, default)

    # def get_dynamic(
    #     self,
    #     key: str,
    #     default: Any = None,
    # ) -> Any:
    #     persistent = self.load()
    #     return persistent.dynamic.get(key, default)

    # def update_dynamic(
    #     self,
    #     updates: dict[str, Any],
    # ) -> bool:
    #     """
    #     Merge values into the dynamic section.

    #     Existing keys are replaced; unrelated dynamic keys remain intact.
    #     """

    #     persistent = self.load()
    #     persistent.dynamic.update(updates)

    #     return self.save(persistent)

    # def replace_dynamic(
    #     self,
    #     dynamic: dict[str, Any],
    # ) -> bool:
    #     """
    #     Replace the complete dynamic section.
    #     """

    #     persistent = self.load()
    #     persistent.dynamic = dict(dynamic)

    #     return self.save(persistent)

    # def replace_static(
    #     self,
    #     static: dict[str, Any],
    # ) -> bool:
    #     """
    #     Replace the complete static section.

    #     This method exists for the promotion mechanism.

    #     Normal callers should not directly modify static information.
    #     """

    #     persistent = self.load()
    #     persistent.static = dict(static)

    #     return self.save(persistent)

    def snapshot(self) -> PersistentFile:
        """
        Return the current state without exposing mutable internal state.
        """

        persistent = self.load()

        return PersistentFile(
            static=dict(persistent.static),
            dynamic=dict(persistent.dynamic),
        )

    @classmethod
    def _from_dict(
        cls,
        data: Any,
    ) -> PersistentFile:
        if not isinstance(data, dict):
            return PersistentFile()

        static = data.get("static", {})
        dynamic = data.get("dynamic", {})

        if not isinstance(static, dict):
            static = {}

        if not isinstance(dynamic, dict):
            dynamic = {}

        return PersistentFile(
            static=dict(static),
            dynamic=dict(dynamic),
        )

    @classmethod
    def _to_dict(
        cls,
        persistent: PersistentFile,
    ) -> dict[str, Any]:
        return {
            "version": cls.VERSION,
            "static": dict(persistent.static),
            "dynamic": dict(persistent.dynamic),
        }