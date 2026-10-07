import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from storage import Event


class SpoolWriter:
    """Appends events to open/current.jsonl and rotates it into pending/."""

    def __init__(self, spool_dir: Path, rotate_mb: int, rotate_minutes: int):
        self.root = Path(spool_dir)
        self.open_dir = self.root / "open"
        self.pending_dir = self.root / "pending"
        for d in (self.open_dir, self.pending_dir, self.root / "done", self.root / "dead"):
            d.mkdir(parents=True, exist_ok=True)

        self.current = self.open_dir / "current.jsonl"
        self.rotate_bytes = rotate_mb * 1024 * 1024
        self.rotate_seconds = rotate_minutes * 60
        self._f = None
        self._opened_at = 0.0
        self._size = 0

    def recover(self) -> None:
        """Call once at startup: rotate whatever a crash left behind, dropping a torn last line."""
        if not self.current.exists():
            return

        data = self.current.read_bytes()
        data = data[: data.rfind(b"\n") + 1]
        if data:
            self.current.write_bytes(data)
            self._move_to_pending()
        else:
            self.current.unlink()

    def write(self, event: Event) -> None:
        line = event.model_dump_json().encode("utf-8") + b"\n"

        if self._f is None:
            self._f = open(self.current, "ab")
            self._opened_at = time.monotonic()
            self._size = 0

        self._f.write(line)
        self._f.flush()
        os.fsync(self._f.fileno())
        self._size += len(line)

        if (
            self._size >= self.rotate_bytes
            or time.monotonic() - self._opened_at >= self.rotate_seconds
        ):
            self.close()

    def close(self) -> None:
        """Move the open file to pending/, so the ingestor only ever sees complete files."""
        if self._f is None:
            return
        self._f.close()
        self._f = None
        self._move_to_pending()

    def _move_to_pending(self) -> None:
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"
        os.replace(self.current, self.pending_dir / name)


async def run_writer(queue: asyncio.Queue, writer: SpoolWriter) -> None:
    while True:
        event = await queue.get()
        try:
            await asyncio.to_thread(writer.write, event)
        finally:
            queue.task_done()