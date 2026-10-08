from __future__ import annotations

import asyncio
import sys

import pytest

import supervisor
from supervisor import Backoff, ServiceSpec, rotate_log, supervise

PY = sys.executable


def fast_backoff():
    return Backoff(first=0.05, cap=0.2, steady_after=5.0)


async def wait_until(predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.02)


# ---------- backoff ----------

def test_backoff_doubles_to_the_cap_and_resets_after_a_steady_run():
    b = Backoff(first=1.0, cap=8.0, steady_after=60.0)

    assert [b.after_run(0.1) for _ in range(5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert b.after_run(120.0) == 1.0  # it ran for two minutes: start over
    assert b.after_run(0.1) == 2.0


# ---------- supervising ----------

async def test_a_crashing_process_is_restarted(tmp_path):
    marker = tmp_path / "starts.txt"
    code = f"open(r'{marker}', 'a').write('x'); raise SystemExit(3)"
    stop = asyncio.Event()

    task = asyncio.create_task(
        supervise([ServiceSpec("crashy", [PY, "-c", code])], stop, log_dir=tmp_path / "logs",
                  cwd=tmp_path, backoff_factory=fast_backoff)
    )
    await wait_until(lambda: marker.exists() and len(marker.read_text()) >= 3)
    stop.set()
    await asyncio.wait_for(task, timeout=10)

    assert len(marker.read_text()) >= 3  # started, crashed, restarted, crashed, ...


async def test_stop_ends_a_running_process_and_returns(tmp_path):
    ready = tmp_path / "ready"
    code = f"open(r'{ready}', 'w').write('up')\nimport time\nwhile True: time.sleep(0.1)"
    stop = asyncio.Event()

    task = asyncio.create_task(
        supervise([ServiceSpec("sleeper", [PY, "-c", code])], stop, log_dir=tmp_path / "logs",
                  cwd=tmp_path, backoff_factory=fast_backoff)
    )
    await wait_until(ready.exists)
    stop.set()
    await asyncio.wait_for(task, timeout=15)  # supervise returns only after the child is gone


async def test_services_run_independently_and_output_is_logged(tmp_path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    specs = [
        ServiceSpec("alpha", [PY, "-c", f"print('hello from alpha'); open(r'{out_a}', 'w').write('1')\nimport time\ntime.sleep(60)"]),
        ServiceSpec("beta", [PY, "-c", f"open(r'{out_b}', 'w').write('1')\nimport time\ntime.sleep(60)"]),
    ]
    stop = asyncio.Event()

    task = asyncio.create_task(
        supervise(specs, stop, log_dir=tmp_path / "logs", cwd=tmp_path, backoff_factory=fast_backoff)
    )
    await wait_until(lambda: out_a.exists() and out_b.exists())
    await wait_until(lambda: "hello from alpha" in (tmp_path / "logs" / "alpha.log").read_text())
    stop.set()
    await asyncio.wait_for(task, timeout=15)

    assert (tmp_path / "logs" / "beta.log").exists()


async def test_a_missing_program_does_not_stop_the_others(tmp_path):
    ok = tmp_path / "ok"
    specs = [
        ServiceSpec("ghost", [str(tmp_path / "does-not-exist.exe")]),
        ServiceSpec("fine", [PY, "-c", f"open(r'{ok}', 'w').write('1')\nimport time\ntime.sleep(60)"]),
    ]
    stop = asyncio.Event()

    task = asyncio.create_task(
        supervise(specs, stop, log_dir=tmp_path / "logs", cwd=tmp_path, backoff_factory=fast_backoff)
    )
    await wait_until(ok.exists)
    stop.set()
    await asyncio.wait_for(task, timeout=15)


# ---------- log rotation ----------

def test_rotation_shifts_old_logs_and_keeps_a_limited_number(tmp_path):
    log = tmp_path / "server.log"

    for generation in range(1, 6):
        log.write_bytes(f"gen{generation}".encode() * 10)
        rotate_log(log, max_bytes=10, keep=3)
        assert not log.exists()  # the live file moved aside; a new one starts fresh

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["server.log.1", "server.log.2", "server.log.3"]
    assert (tmp_path / "server.log.1").read_text().startswith("gen5")  # newest first
    assert (tmp_path / "server.log.3").read_text().startswith("gen3")


def test_small_logs_are_left_alone(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("tiny")
    rotate_log(log, max_bytes=1000)
    assert log.read_text() == "tiny"
    rotate_log(tmp_path / "missing.log")  # no error for a file that does not exist


# ---------- the real service list ----------

def test_default_services_match_what_we_run_by_hand():
    specs = {s.name: s.command for s in supervisor.default_services()}

    assert specs["server"][1:] == ["run_server.py"]
    assert specs["collector"][1:] == ["-m", "capture", "collect"]
    assert specs["ingestor"][1:] == ["-m", "capture", "ingest"]
    assert all(c[0].lower().endswith("python.exe") for c in specs.values())  # never pythonw: it has no console


def test_dry_run_starts_nothing(capsys):
    assert supervisor.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "run_server.py" in out and "capture" in out
