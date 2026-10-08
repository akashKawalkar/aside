from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from capture.monitoring import MonitoringFlag
from capture.sampler import sampler


# ---------- the flag file ----------

def test_missing_file_means_recording(tmp_path):
    assert MonitoringFlag(tmp_path / "monitoring.json").enabled() is True


def test_set_then_read_across_instances(tmp_path):
    path = tmp_path / "state" / "monitoring.json"  # parent folder is created

    MonitoringFlag(path).set(False)

    assert MonitoringFlag(path).enabled() is False
    assert json.loads(path.read_text())["enabled"] is False

    MonitoringFlag(path).set(True)
    assert MonitoringFlag(path).enabled() is True


def test_reader_sees_changes_only_after_ttl(tmp_path):
    path = tmp_path / "monitoring.json"
    now = [100.0]
    reader = MonitoringFlag(path, ttl=2.0, clock=lambda: now[0])
    assert reader.enabled() is True

    MonitoringFlag(path).set(False)  # another process pauses

    now[0] += 1.0
    assert reader.enabled() is True  # cached
    now[0] += 1.5
    assert reader.enabled() is False  # re-read


def test_unreadable_file_keeps_last_known_state(tmp_path):
    path = tmp_path / "monitoring.json"
    now = [0.0]
    flag = MonitoringFlag(path, ttl=1.0, clock=lambda: now[0])
    flag.set(False)

    path.write_text("{ not json")
    now[0] += 5
    assert flag.enabled() is False  # a corrupt file must not silently resume recording


def test_no_temp_file_is_left_behind(tmp_path):
    MonitoringFlag(tmp_path / "monitoring.json").set(False)
    assert [p.name for p in tmp_path.iterdir()] == ["monitoring.json"]


# ---------- the sampler honours it ----------

class FakeClock:
    """Each call to now() is 1 ms later. The step must stay below 3x the poll
    interval, or the sampler treats every tick as a sleep gap."""

    def __init__(self):
        self.ticks = 0
        self._t = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)

    def now(self):
        self.ticks += 1
        self._t += timedelta(milliseconds=1)
        return self._t


class FakeProbe:
    def get_foreground(self):
        return "code.exe", "main.py"

    def get_idle_seconds(self):
        return 0.0


async def wait_until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.002)


async def test_sampler_records_nothing_while_paused_and_resumes():
    clock = FakeClock()
    queue: asyncio.Queue = asyncio.Queue()
    paused = {"value": False}

    task = asyncio.create_task(
        sampler(queue, clock, FakeProbe(), 0.002, 300.0, 3600.0, set(), lambda: paused["value"])
    )

    await wait_until(lambda: clock.ticks >= 8)
    assert queue.empty()  # one long segment is still open

    paused["value"] = True
    await wait_until(lambda: not queue.empty())  # pausing closes the open segment
    before_pause = queue.get_nowait()
    assert before_pause.app == "code.exe"

    ticks_at_pause = clock.ticks
    await asyncio.sleep(0.1)  # plenty of poll intervals while paused
    assert clock.ticks == ticks_at_pause  # the sampler did not even look at the window
    assert queue.empty()

    paused["value"] = False
    await wait_until(lambda: clock.ticks >= ticks_at_pause + 6)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)  # shutdown flushes the new segment

    after_resume = queue.get_nowait()
    assert after_resume.ts_start > before_pause.ts_end  # nothing covers the paused stretch
    assert queue.empty()


async def test_sampler_without_a_pause_hook_still_records():
    clock = FakeClock()
    queue: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(sampler(queue, clock, FakeProbe(), 0.002, 300.0, 3600.0, set()))
    await wait_until(lambda: clock.ticks >= 5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert queue.get_nowait().app == "code.exe"
