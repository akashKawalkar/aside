"""
Start the collector, the ingestor and the local server, and keep them running.

    python supervisor.py              run in the foreground (Ctrl+C stops everything)
    pythonw supervisor.py             run with no window (what the logon task uses)
    python supervisor.py --status     is it running?
    python supervisor.py --stop       stop it and the three processes
    python supervisor.py --dry-run    print what would be started, start nothing
    python supervisor.py --install    start it automatically when you log in
    python supervisor.py --uninstall  undo --install

A process that exits is restarted after a pause that doubles up to a minute and
resets once it has run steadily. Each process logs to logs/<name>.log.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
PID_FILE = ROOT / "data" / "supervisor.pid"

TASK_NAME = "PersonalAgentSupervisor"
STARTUP_SHORTCUT = "Personal Agent.lnk"

LOG_ROTATE_BYTES = 5 * 1024 * 1024
LOG_KEEP = 3
CREATE_NO_WINDOW = 0x08000000  # Windows: no console window for a child

log = logging.getLogger("supervisor")


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: list[str]


@dataclass
class Backoff:
    first: float = 1.0
    cap: float = 60.0
    steady_after: float = 60.0  # a run at least this long counts as healthy
    _next: float = field(default=0.0, init=False)

    def after_run(self, ran_for: float) -> float:
        """Seconds to wait before the next start."""
        if ran_for >= self.steady_after or self._next == 0.0:
            self._next = self.first
        else:
            self._next = min(self._next * 2, self.cap)
        return self._next


def python_for_children() -> str:
    """python.exe, even when the supervisor itself runs under pythonw.exe."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console if console.exists() else exe)


def default_services() -> list[ServiceSpec]:
    python = python_for_children()
    return [
        ServiceSpec("server", [python, "run_server.py"]),
        ServiceSpec("collector", [python, "-m", "capture", "collect"]),
        ServiceSpec("ingestor", [python, "-m", "capture", "ingest"]),
    ]


def rotate_log(path: Path, max_bytes: int = LOG_ROTATE_BYTES, keep: int = LOG_KEEP) -> None:
    """Move an oversized log to .1, shifting older ones up and dropping the oldest."""
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return

        for n in range(keep - 1, 0, -1):
            older = path.with_name(f"{path.name}.{n}")
            if older.exists():
                os.replace(older, path.with_name(f"{path.name}.{n + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))

        overflow = path.with_name(f"{path.name}.{keep + 1}")
        if overflow.exists():
            overflow.unlink()
    except OSError:
        log.exception("could not rotate %s", path)


async def run_service(
    spec: ServiceSpec,
    stop: asyncio.Event,
    *,
    log_dir: Path,
    cwd: Path,
    backoff: Backoff,
) -> None:
    """Run one process until `stop` is set, restarting it whenever it exits."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{spec.name}.log"
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0

    while not stop.is_set():
        rotate_log(log_path)
        started = asyncio.get_running_loop().time()

        with open(log_path, "ab") as sink:
            try:
                process = await asyncio.create_subprocess_exec(
                    *spec.command,
                    cwd=cwd,
                    env=env,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=flags,
                )
            except OSError:
                log.exception("%s: could not start", spec.name)
                process = None

            if process is not None:
                log.info("%s: started (pid %s)", spec.name, process.pid)
                waiter = asyncio.create_task(process.wait())
                stopper = asyncio.create_task(stop.wait())
                await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                stopper.cancel()

                if stop.is_set():
                    await terminate(process, spec.name)
                    waiter.cancel()
                    return

                log.warning("%s: exited with code %s", spec.name, waiter.result())

        delay = backoff.after_run(asyncio.get_running_loop().time() - started)
        log.info("%s: restarting in %.0fs", spec.name, delay)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass


async def terminate(process: asyncio.subprocess.Process, name: str, grace: float = 8.0) -> None:
    if process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
    except asyncio.TimeoutError:
        log.warning("%s: did not stop in %.0fs, killing it", name, grace)
        process.kill()
        await process.wait()
    log.info("%s: stopped", name)


async def supervise(
    specs: list[ServiceSpec],
    stop: asyncio.Event,
    *,
    log_dir: Path = LOG_DIR,
    cwd: Path = ROOT,
    backoff_factory=Backoff,
) -> None:
    await asyncio.gather(
        *(
            run_service(spec, stop, log_dir=log_dir, cwd=cwd, backoff=backoff_factory())
            for spec in specs
        )
    )


# ---------- single instance, status, stop ----------

def running_pid() -> int | None:
    """The supervisor's pid if one is alive, else None."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None

    try:
        import psutil

        process = psutil.Process(pid)
        if "supervisor" in " ".join(process.cmdline()).lower():
            return pid
    except Exception:
        pass
    return None


def stop_running() -> bool:
    pid = running_pid()
    if pid is None:
        print("The supervisor is not running.")
        PID_FILE.unlink(missing_ok=True)
        return False

    # /T ends its three processes with it.
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    PID_FILE.unlink(missing_ok=True)
    print(f"Stopped the supervisor (pid {pid}) and its processes.")
    return True


# ---------- start at logon ----------

def _pythonw() -> str:
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    return str(windowless if windowless.exists() else exe)


def install_logon_task() -> None:
    command = f'"{_pythonw()}" "{Path(__file__).resolve()}"'
    result = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "ONLOGON", "/TR", command, "/RL", "LIMITED", "/F"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"Created the scheduled task '{TASK_NAME}': the supervisor starts when you log in.")
        return

    print("Task Scheduler refused (it often needs an administrator prompt):")
    print(" ", (result.stderr or result.stdout).strip())
    print("Falling back to a shortcut in your Startup folder instead.")

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"(Join-Path ([Environment]::GetFolderPath('Startup')) '{STARTUP_SHORTCUT}'));"
        f"$s.TargetPath = '{_pythonw()}'; $s.Arguments = '\"{Path(__file__).resolve()}\"';"
        f"$s.WorkingDirectory = '{ROOT}'; $s.Save()"
    )
    fallback = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    if fallback.returncode == 0:
        print("Created the Startup shortcut: the supervisor starts when you log in.")
    else:
        print("That failed too:", fallback.stderr.strip())
        sys.exit(1)


def uninstall_logon_task() -> None:
    removed = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    shortcut = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "$p = Join-Path ([Environment]::GetFolderPath('Startup')) "
            f"'{STARTUP_SHORTCUT}'; if (Test-Path $p) {{ Remove-Item $p -Force; 'removed' }}",
        ],
        capture_output=True,
        text=True,
    )
    if removed.returncode == 0:
        print(f"Removed the scheduled task '{TASK_NAME}'.")
    if "removed" in shortcut.stdout:
        print("Removed the Startup shortcut.")
    if removed.returncode != 0 and "removed" not in shortcut.stdout:
        print("Nothing was installed.")


# ---------- entry point ----------

def run_foreground() -> int:
    if running_pid() is not None:
        print("The supervisor is already running (python supervisor.py --stop to stop it).")
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    rotate_log(LOG_DIR / "supervisor.log")
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_DIR / "supervisor.log", encoding="utf-8")]
    if sys.stderr is not None and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    PID_FILE.write_text(str(os.getpid()))

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_stop(*_):
            loop.call_soon_threadsafe(stop.set)

        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), request_stop)

        log.info("supervisor started (pid %s)", os.getpid())
        await supervise(default_services(), stop)
        log.info("supervisor stopped")

    try:
        asyncio.run(main())
    finally:
        PID_FILE.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="say whether it is running")
    group.add_argument("--stop", action="store_true", help="stop it and its processes")
    group.add_argument("--dry-run", action="store_true", help="print what would start, start nothing")
    group.add_argument("--install", action="store_true", help="start it when you log in")
    group.add_argument("--uninstall", action="store_true", help="undo --install")
    args = parser.parse_args(argv)

    if args.status:
        pid = running_pid()
        print(f"running (pid {pid})" if pid else "not running")
        return 0 if pid else 1
    if args.stop:
        stop_running()
        return 0
    if args.dry_run:
        for spec in default_services():
            print(f"{spec.name:10} {' '.join(spec.command)}   -> logs/{spec.name}.log")
        return 0
    if args.install:
        install_logon_task()
        return 0
    if args.uninstall:
        uninstall_logon_task()
        return 0

    return run_foreground()


if __name__ == "__main__":
    sys.exit(main())
