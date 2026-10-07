# scripts/ui_check/app_server.py — the real app and the real database, on a TEST port, with the model and the clock
# replaced so a UI check never touches a real day or a real API:
#   default   FakeClient with scripted schedule replies, clock fixed at 2031-03-03 12:00 IST ("tomorrow" = 2031-03-04)
#   UI_BAD=1  the fake model answers with something unusable (to check the failure path)
#   UI_REAL=1 NO fake model: the real Gemini client, still behind the approval gate and the daily cap (used by M6c)
#   UI_NOW=real  use the real clock instead of the fixed one
# Never uses 8787. Port from UI_PORT (default 8788).
from __future__ import annotations

import asyncio
import json
import os
import selectors
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import uvicorn  # noqa: E402

import capture.server as srv  # noqa: E402
from llm.client import ModelProfile  # noqa: E402
from llm.fake import FakeClient  # noqa: E402
from schedule_gen.model import IST  # noqa: E402

PORT = int(os.environ.get("UI_PORT", "8788"))
assert PORT != 8787, "8787 is the user's own server"


def reply(*entries):
    return json.dumps({"entries": [{"title": t, "start": s, "end": e, "reason": r} for t, s, e, r in entries]})


GOOD = reply(("zz Gym", "07:00", "08:00", "your usual weekday routine"), ("zz Deep work", "09:00", "12:00", "weekday focus block"),
             ("zz Tennis", "18:30", "20:00", "Tuesday evening with Rahul"), ("zz Midnight", "23:30", "23:45", "too late"))
REVISED = reply(("zz Reading", "19:30", "20:30", "you asked for a quieter evening"))
PROFILE = ModelProfile(name="fake-gen", provider="fake", window=1_000_000, input_cost=1.0, output_cost=2.0, supports_tools=True, supports_json=True)

app = srv.app
if not os.environ.get("UI_REAL"):
    script = ["Sorry, I cannot plan that."] if os.environ.get("UI_BAD") else [GOOD, REVISED]
    app.state.llm_profile = PROFILE
    app.state.llm_client = FakeClient(script, profile=PROFILE)
if os.environ.get("UI_NOW") != "real":
    app.state.now_fn = lambda: datetime(2031, 3, 3, 12, 0, tzinfo=IST)


async def serve():
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")).serve()


if __name__ == "__main__":
    loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    asyncio.set_event_loop(loop)
    loop.run_until_complete(serve())
