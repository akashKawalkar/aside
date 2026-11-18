# scripts/replay_plan.py — rebuild the planner's input as a night run saw it and ask the real model again.
#
#   .venv/Scripts/python.exe scripts/replay_plan.py --at 2026-11-18T00:08:00+00:00 --model gemini-3.1-flash-lite
#
# Reads the live database (gather() only reads, plus the practiced-day cache it rebuilds anyway) and makes real model calls
# through the approval gate and the daily cap. It never saves a draft or touches the calendar. Data that changed since `--at`
# (tasks finished, notes added) is read as it is now. The prompt and every attempt are written to docs/prompt_experiments/
# as v<N>_system.txt, v<N>_user.txt and v<N>_<label>-result.json (the user file and results hold personal data: keep them local).
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import storage  # noqa: E402
from config import load_config  # noqa: E402
from llm.approved import Grant, approved  # noqa: E402
from llm.client import Message  # noqa: E402
from llm.factory import create_client, load_dotenv  # noqa: E402,F401  (importing the factory loads .env)
from llm.quota import provider_day_start  # noqa: E402
from practiced.llmcall import BadReply, call_json  # noqa: E402
from practiced.plancheck import IST, check, repair  # noqa: E402
from practiced.planning import PROMPT_VERSION, build_messages  # noqa: E402
from practiced.planrun import MAX_TOKENS, RETRIES, TEMPERATURE, gather  # noqa: E402
from storage.repo.llm_trace import count_llm_traces_since, insert_llm_trace  # noqa: E402

OUT = ROOT / "docs" / "prompt_experiments"
SPARE = 3      # calls left for the person's own use


async def main(a) -> None:
    pool = storage.make_pool()
    await pool.open()
    try:
        inp, summary = await gather(pool, datetime.fromisoformat(a.at))
        messages = build_messages(inp)
        (OUT / f"v{PROMPT_VERSION}_system.txt").write_text(messages[0].content, encoding="utf-8")
        (OUT / f"v{PROMPT_VERSION}_user.txt").write_text(messages[1].content, encoding="utf-8")

        cap = load_config().llm.daily_call_cap
        count = lambda: count_llm_traces_since(pool, provider_day_start(datetime.now(IST)))  # noqa: E731
        if await count() + RETRIES + 1 > cap - SPARE:
            sys.exit(f"Not enough calls left today ({await count()} of {cap} used; {SPARE} kept spare).")

        async def save_span(row):
            await insert_llm_trace(pool, row)
        client = create_client(a.model, count_today=count, daily_call_cap=cap, background_enabled=True, save_span=save_span,
                               timeout=load_config().llm.attempt_timeout)

        attempts = []
        with approved(Grant("background", operation="plan_replay")):
            for n in range(RETRIES + 1):
                try:
                    out, done = await call_json(client, messages, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
                    reply = done.text
                    fixed = repair(out, inp)
                    problems = check(out, inp)
                    usage = {"model": done.model, "input_tokens": done.usage.input_tokens, "output_tokens": done.usage.output_tokens}
                except BadReply as e:
                    out, reply, fixed, problems, usage = None, e.text, [], [str(e)], {}
                except Exception as e:  # provider trouble: record it and stop
                    attempts.append({"attempt": n, "error": f"{type(e).__name__}: {e}"})
                    break
                attempts.append({"attempt": n, **usage, "repairs": fixed, "problems": problems, "answer": out})
                print(f"attempt {n}: {usage.get('model')} repairs={len(fixed)} problems={len(problems)}")
                for p in problems:
                    print("   -", p)
                if not problems:
                    break
                messages = messages + [Message("assistant", reply), Message("user", "That plan cannot be used:\n- " + "\n- ".join(problems) +
                                                                            "\nAnswer again with the one JSON object, fixed. Keep everything that was not wrong.")]
        path = OUT / f"v{PROMPT_VERSION}_{a.label or a.model}-result.json"
        path.write_text(json.dumps({"at": a.at, "day": inp.day.isoformat(), "model": a.model, "summary": summary, "attempts": attempts},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("written", path)
    finally:
        await pool.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--at", required=True, help="the run time to rebuild, ISO with offset")
    p.add_argument("--model", required=True, help="model profile name, e.g. gemini-3.6-flash or gemini-3.1-flash-lite")
    p.add_argument("--label", default="", help="result file label (default: the model name)")
    if sys.platform == "win32":     # psycopg async needs the selector loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(p.parse_args()))
