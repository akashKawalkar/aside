# scripts/ui_check/run.py — run the extension's UI checks in real headless Chrome.
#
#   .venv/Scripts/python.exe scripts/ui_check/run.py                 # every scenario
#   .venv/Scripts/python.exe scripts/ui_check/run.py --only main     # one (main | chat | edge)
#   .venv/Scripts/python.exe scripts/ui_check/run.py --shots shots/  # keep the screenshots there
#
# It copies extension1/ to a temp folder with the API address pointed at the TEST server, serves that on 8790, runs the
# real app on 8788 against the real database but with a fake model and a fixed 2031 clock, drives Chrome (9333), then
# stops everything it started and removes every row it created. It refuses to run if a port is taken and never uses 8787.
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import fixtures  # noqa: E402
from cdp import PORT as CHROME_PORT, Browser  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

API_PORT, STATIC_PORT = 8788, 8790
URLS = {"panel": f"http://localhost:{STATIC_PORT}/sidepanel/panel.html", "settings": f"http://localhost:{STATIC_PORT}/settings/settings.html",
        "api": f"http://localhost:{API_PORT}"}


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(cmd, cwd, env_extra=None, log=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT)


def wait_for(url, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


def stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


async def run(names, shots: Path) -> int:
    for port in (API_PORT, STATIC_PORT, CHROME_PORT):
        if port_in_use(port):
            print(f"port {port} is already in use; stop whatever owns it first (this tool never kills a process it did not start)")
            return 2

    work = Path(tempfile.mkdtemp(prefix="ui-check-"))
    ext = work / "ext"
    shutil.copytree(ROOT / "extension1", ext)
    api_js = ext / "shared" / "api.js"
    api_js.write_text(api_js.read_text(encoding="utf-8").replace("localhost:8787", f"localhost:{API_PORT}"), encoding="utf-8")
    shots.mkdir(parents=True, exist_ok=True)

    static = start([sys.executable, "-m", "http.server", str(STATIC_PORT)], cwd=ext)
    browser = server = None
    failures = 0
    try:
        wait_for(f"http://localhost:{STATIC_PORT}/sidepanel/panel.html", 15)
        browser = await Browser().start()
        for name in names:
            scenario, env = SCENARIOS[name]
            print(f"\n[{name}] {scenario.__doc__.strip().splitlines()[0]}")
            await fixtures.seed()
            server = start([sys.executable, str(HERE / "app_server.py")], cwd=ROOT, env_extra=env, log=open(work / f"server-{name}.log", "wb"))
            try:
                wait_for(f"http://localhost:{API_PORT}/health", 90)
                await scenario(browser, URLS, shots)
            except Exception:
                failures += 1
                traceback.print_exc()
            finally:
                stop(server)
                await fixtures.clean()
    finally:
        if browser:
            await browser.stop()
        stop(static)
        await fixtures.clean()
        shutil.rmtree(work, ignore_errors=True)
    print(f"\n{'FAILED: ' + str(failures) + ' scenario(s)' if failures else 'all scenarios passed'}; screenshots in {shots}")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=sorted(SCENARIOS), action="append", help="run just this scenario (repeatable)")
    parser.add_argument("--shots", type=Path, default=Path(tempfile.gettempdir()) / "ui-check-shots", help="where to keep screenshots")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(run(args.only or list(SCENARIOS), args.shots)))


if __name__ == "__main__":
    main()
