from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capture.spool import SpoolWriter
from storage import Event, parse_spool_file

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def event(n: int) -> Event:
    return Event(source="laptop", kind="app_focus", app=f"app{n}.exe", window_title=None, domain=None,
                 ts_start=T0 + timedelta(seconds=n), ts_end=T0 + timedelta(seconds=n + 1), payload={})


def test_many_events_go_into_one_file_and_survive_close(tmp_path):
    # Regression: write() once crashed from its second event on (a stray local `import os`).
    writer = SpoolWriter(tmp_path, rotate_mb=10, rotate_minutes=60)
    for n in range(5):
        writer.write(event(n))
    writer.close()

    files = list((tmp_path / "pending").glob("*.jsonl"))
    assert len(files) == 1
    events, bad = parse_spool_file(files[0])
    assert [e.app for e in events] == [f"app{n}.exe" for n in range(5)] and bad == []


def test_a_file_rotates_when_it_reaches_the_size_limit(tmp_path):
    writer = SpoolWriter(tmp_path, rotate_mb=1, rotate_minutes=60)
    writer.rotate_bytes = 300                       # tiny, so a couple of events fill a file
    for n in range(6):
        writer.write(event(n))
    writer.close()
    assert len(list((tmp_path / "pending").glob("*.jsonl"))) >= 2
