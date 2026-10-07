# scripts/live_draft_check.py — M6c: what does the REAL model do with a schedule-draft request?
#
#   .venv/Scripts/python.exe scripts/live_draft_check.py                    # stage only: print the exact prompt, send NOTHING
#   .venv/Scripts/python.exe scripts/live_draft_check.py --send             # send ONE call, show the reply and what the checks made of it
#   .venv/Scripts/python.exe scripts/live_draft_check.py --send --revise "keep the evening free"   # then one revision (a 2nd call)
#   add --no-tasks to leave the pending tasks out of the prompt (this run only; no task is changed or deleted)
#
# The prompt contains the user's REAL persistent file and pending tasks, and a free-tier call may be used by the provider
# to improve its products, so a send is a deliberate act: run it without --send first and read the prompt.
# Safety: the app runs in this process with the clock fixed at 2031-03-03, so the draft is for 2031-03-04 and never touches a
# real day; the draft and the instruction are deleted afterwards. The llm_trace and compile_log rows stay (they are the
# audit record of a real call and count against the daily cap). Goes through the normal approval + quota gate.
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from httpx import ASGITransport, AsyncClient  # noqa: E402

import capture.llm_routes as llm_routes  # noqa: E402
import capture.server as srv  # noqa: E402
import storage  # noqa: E402
from config import load_config  # noqa: E402
from llm.client import load_profile  # noqa: E402
from schedule_gen.model import IST  # noqa: E402

DAY = "2031-03-04"
DEFAULT_INSTRUCTION = "Plan a normal weekday: a workout in the morning, one long focus block, the evening free."


def show_prompt(preview: dict) -> str:
    print(f"\nmodel: {preview['model']}   ~{preview['tokens_estimate']} input tokens   worst-case cost ~${preview['cost_estimate_usd']:.4f}\n")
    text = ""
    for message in preview["messages"]:
        block = f"[{message['role']}]\n{message['content']}\n"
        text += block
        print(block)
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    print(f"prompt fingerprint: {digest}   (the same prompt gives the same fingerprint, so a --send run can be matched to what you read)\n")
    return digest


def show_draft(draft: dict) -> None:
    print(f"draft v{draft['version']} ({draft['source']}, {draft['model']}): {len(draft['entries'])} block(s) kept, {len(draft['rejected'])} rejected")
    for entry in draft["entries"]:
        print(f"  {entry['start_at'][11:16]}-{entry['end_at'][11:16]}  {entry['title']:<32} [{entry['state']}{', locked' if entry['locked'] else ''}]  {entry['reason']}")
    for item in draft["rejected"]:
        e = item["entry"]
        print(f"  REJECTED {e.get('title', '?')}: {item['reason']}")


async def main(args) -> int:
    pool = storage.make_pool()
    await pool.open()
    app = srv.app
    app.state.db_pool = pool
    app.state.now_fn = lambda: datetime(2031, 3, 3, 12, 0, tzinfo=IST)     # NOT the real day; see the header
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://live")
    cfg = load_config()
    instructions: list[str] = []
    if args.no_tasks:
        async def no_tasks(pool, *, status="pending"):
            return []
        storage.list_tasks = no_tasks          # in this process only: the gather step sees no tasks

    async def post(path, body=None):
        return (await http.post(path, json=body or {})).json()

    async def cleanup():
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM schedule_draft WHERE target_day = %s", (DAY,))
                for text in instructions:
                    await cur.execute("DELETE FROM instruction_records WHERE text = %s", (text,))

    try:
        profile = load_profile()
        used = await llm_routes.calls_today(pool)
        print(f"model profile: {profile.name} ({profile.provider}); calls today {used}/{cfg.llm.daily_call_cap}; "
              f"API key present: {bool(os.environ.get('GEMINI_API_KEY', '').strip())}")
        await cleanup()

        instructions.append(args.instruction)
        staged = await post("/schedule/draft/generate", {"instruction": args.instruction})
        if staged["status"] != "ok":
            print("could not stage:", staged["message"])
            return 1
        data = staged["data"]
        fingerprint = show_prompt(data["preview"])

        if not args.send:
            await post(f"/llm/reject/{data['pending_id']}")
            print("NOT SENT. Nothing left this machine. Re-run with --send to send exactly this prompt.")
            return 0

        print("SENDING ONE REAL CALL ...")
        approved = await post(f"/llm/approve/{data['pending_id']}")
        print(f"result: {approved['status']}   {approved['message']}")
        d = approved.get("data") or {}
        if "text" in d:
            print(f"model: {d['model']}   tokens in/out: {d['tokens_used']['input']}/{d['tokens_used']['output']}   hidden thinking: {d.get('thinking_tokens')}   "
                  f"latency: {d['latency_ms']:.0f} ms   finish: {d.get('finish_reason')}   fell back from: {d.get('fell_back_from')}")
            print("\n--- raw reply ---\n" + (d["text"] or "(empty)") + "\n--- end ---\n")
        elif "raw_text" in d:
            print("\n--- raw reply (unusable) ---\n" + (d["raw_text"] or "(empty)") + "\n--- end ---\n")
        if approved["status"] != "ok":
            return 1
        draft = d["result"]["draft"]
        show_draft(draft)

        if args.revise:
            instructions.append(args.revise)
            print(f"\nREVISING: {args.revise!r}")
            staged = await post(f"/schedule/draft/{draft['id']}/revise", {"instruction": args.revise})
            if staged["status"] != "ok":
                print("could not stage the revision:", staged["message"])
                return 1
            show_prompt(staged["data"]["preview"])
            print("SENDING THE REVISION ...")
            approved = await post(f"/llm/approve/{staged['data']['pending_id']}")
            print(f"result: {approved['status']}   {approved['message']}")
            d = approved.get("data") or {}
            if "text" in d:
                print(f"tokens in/out: {d['tokens_used']['input']}/{d['tokens_used']['output']}   latency: {d['latency_ms']:.0f} ms")
                print("\n--- raw reply ---\n" + (d["text"] or "(empty)") + "\n--- end ---\n")
            if approved["status"] == "ok":
                show_draft(d["result"]["draft"])
        return 0
    finally:
        await cleanup()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="actually send the call (default: stage and print only)")
    parser.add_argument("--revise", help="after the first call, send one revision with this instruction")
    parser.add_argument("--instruction", default=DEFAULT_INSTRUCTION)
    parser.add_argument("--no-tasks", action="store_true", help="leave pending tasks out of the prompt (this run only)")
    ns = parser.parse_args()
    if ns.revise and not ns.send:
        parser.error("--revise needs --send")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main(ns)))
