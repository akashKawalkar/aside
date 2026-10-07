# scripts/api_probe.py — measure how the real Gemini API behaves, so the call design rests on evidence, not guesses.
#
#   .venv/Scripts/python.exe scripts/api_probe.py --models                     # list models (no generation, no quota)
#   .venv/Scripts/python.exe scripts/api_probe.py --run tiny_default,tiny_low  # run named experiments
#   .venv/Scripts/python.exe scripts/api_probe.py --list                       # name every experiment
#
# Every generation goes through ApprovedClient with a `probe` grant, so it is gated by the daily cap, traced in llm_trace and
# counted like any other call. Prompts are SYNTHETIC (no personal data). Each result, with the full raw response, is appended
# to data/api_probe/results.jsonl so conclusions can be re-read later. Nothing here changes application behaviour.
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import aiohttp  # noqa: E402

import capture.llm_routes as llm_routes  # noqa: E402
import storage  # noqa: E402
from config import load_config  # noqa: E402
from llm.adapters.openai_compatible import HttpResponse, OpenAICompatibleClient, aiohttp_post  # noqa: E402
from llm.approved import ApprovedClient, Grant, approved  # noqa: E402
from llm.client import Message, ModelProfile  # noqa: E402
from llm.factory import load_dotenv  # noqa: E402,F401  (importing the factory loads .env)
from schedule_gen.model import Rules  # noqa: E402
from schedule_gen.parse import ParseError, parse_entries  # noqa: E402
from schedule_gen.prompt import build_messages  # noqa: E402
from schedule_gen.validator import validate  # noqa: E402
from schedule_gen.model import Block, IST  # noqa: E402

BASE = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
RESULTS = ROOT / "data" / "api_probe" / "results.jsonl"
DAY = date(2031, 3, 4)
RULES = Rules(day=DAY)
KEY = (os.environ.get("GEMINI_API_KEY") or "").strip().strip("'\"")

# ---------- synthetic prompts ----------

CONTEXT = """## instructions
- Plan a normal weekday: a workout in the morning, one long focus block, the evening free.

## persistent file
### Identity
- Works as a software engineer in Pune, India.
### Preferences
- Never schedule anything before 07:00.
### Routine
- Weekday mornings: gym 07:00-08:00, then deep work until 12:30.

## fixed
- 15:00-15:30 Dentist

## weekday template
### Tuesday 25 Feb
- 07:00-08:00 Gym
- 09:00-12:30 Deep work
- 18:30-20:00 Tennis

## free slots
- 07:00-15:00, 15:30-23:00"""
FIXED = [Block(datetime(2031, 3, 4, 15, 0, tzinfo=IST), datetime(2031, 3, 4, 15, 30, tzinfo=IST), "Dentist")]
SCHEDULE = build_messages(CONTEXT, RULES, "Plan a normal weekday: a workout in the morning, one long focus block, the evening free.")
CHAT = [
    Message("system", "## current session\n- what should I focus on this week?\n\n## persistent file\n### Identity\n- Works as a software engineer in Pune, India.\n### Preferences\n- Replies in under 80 words unless asked for detail.\n### Goals\n- Ship the side project by December."),
    Message("user", "what should I focus on this week?"),
]
TINY = [Message("user", "Reply with exactly the single word: OK")]

SCHEMA = {"type": "json_schema", "name": "schedule", "schema": {
    "type": "object", "additionalProperties": False, "required": ["entries"],
    "properties": {"entries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["title", "start", "end", "reason"],
        "properties": {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "reason": {"type": "string"}}}}}}}

M36, M38, LITE = "gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite"


def X(model, messages, *, max_tokens, temperature=0.3, response_format=None, extra=None, note=""):
    return dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, response_format=response_format, extra=extra or {}, note=note)


JSON = {"type": "json_object"}
EXPERIMENTS = {
    # --- is it thinking? does the budget include it? ---
    "tiny_default": X(M36, TINY, max_tokens=300, temperature=1.0, note="baseline: trivial prompt, default thinking"),
    "tiny_cap50": X(M36, TINY, max_tokens=50, temperature=1.0, note="does a small cap starve the visible answer?"),
    "tiny_low": X(M36, TINY, max_tokens=300, temperature=1.0, extra={"reasoning_effort": "low"}),
    "tiny_minimal": X(M36, TINY, max_tokens=300, temperature=1.0, extra={"reasoning_effort": "minimal"}),
    # --- the schedule request, as the app sends it ---
    "sched_default": X(M36, SCHEDULE, max_tokens=1500, response_format=JSON, note="exactly what the app sends today"),
    "sched_low": X(M36, SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "low"}),
    "sched_minimal": X(M36, SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "minimal"}),
    "sched_nojson": X(M36, SCHEDULE, max_tokens=1500, extra={"reasoning_effort": "low"}, note="no response_format: is JSON mode the slow part?"),
    "sched_temp1": X(M36, SCHEDULE, max_tokens=1500, temperature=1.0, response_format=JSON, extra={"reasoning_effort": "low"}),
    "sched_schema": X(M36, SCHEDULE, max_tokens=1500, response_format=SCHEMA, extra={"reasoning_effort": "low"}, note="strict json_schema"),
    # --- other models ---
    "sched_lite": X(LITE, SCHEDULE, max_tokens=1500, response_format=JSON),
    "sched_lite_low": X(LITE, SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "low"}),
    "sched_38_low": X(M38, SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "low"}),
    "sched_38_default": X(M38, SCHEDULE, max_tokens=1500, response_format=JSON),
    # --- under load: is a 503 transient, and is another model less busy? ---
    "sched_37_min": X("gemini-3.7-flash", SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "minimal"}),
    "sched_31lite_min": X("gemini-3.1-flash-lite", SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "minimal"}),
    "sched_36_min_json": X(M36, SCHEDULE, max_tokens=1500, response_format=JSON, extra={"reasoning_effort": "minimal"}, note="the candidate production setting"),
    "tiny_min_now": X(M36, TINY, max_tokens=300, temperature=1.0, extra={"reasoning_effort": "minimal"}, note="baseline latency right now"),
    # --- the chat call that came back truncated ---
    "chat_default": X(M36, CHAT, max_tokens=1000, temperature=0.7, note="the user's chat shape; earlier real call ended finish_reason=length"),
    "chat_low": X(M36, CHAT, max_tokens=1000, temperature=0.7, extra={"reasoning_effort": "low"}),
}

# ---------- machinery ----------


def profile_for(model: str) -> ModelProfile:
    return ModelProfile(name=model, provider="gemini", window=1_048_576, supports_tools=True, supports_json=True)


async def run_one(name: str, spec: dict, pool, timeout: float, cap: int) -> dict:
    captured: list[HttpResponse] = []
    sent: dict = {}

    async def post(url, headers, body, t):
        body = {**body, **spec["extra"]}                # parameters the adapter cannot send yet
        sent.update(body)
        response = await aiohttp_post(url, headers, body, t)
        captured.append(response)
        return response

    async def save_span(row):
        await storage.insert_llm_trace(pool, row)

    inner = OpenAICompatibleClient(profile_for(spec["model"]), base_url=BASE, api_key=KEY, post=post, timeout=timeout)
    client = ApprovedClient(inner, count_today=lambda: llm_routes.calls_today(pool), daily_call_cap=cap, save_span=save_span)

    record = {"ts": datetime.now(IST).isoformat(), "name": name, "model": spec["model"], "note": spec["note"], "max_tokens": spec["max_tokens"],
              "temperature": spec["temperature"], "response_format": (spec["response_format"] or {}).get("type"), "extra": spec["extra"]}
    started = time.perf_counter()
    try:
        with approved(Grant("user", operation="probe")):
            completion = await client.complete(spec["messages"], response_format=spec["response_format"], max_tokens=spec["max_tokens"], temperature=spec["temperature"])
        record.update(ok=True, latency_s=round(time.perf_counter() - started, 2), text=completion.text, finish_reason=completion.finish_reason)
    except Exception as exc:                            # a probe reports every failure; none is hidden
        record.update(ok=False, latency_s=round(time.perf_counter() - started, 2), error=f"{type(exc).__name__}: {exc}")
    if captured:
        raw = captured[-1]
        record["http_status"] = raw.status
        body = raw.body if isinstance(raw.body, (dict, list)) else None
        if isinstance(body, dict):
            record["usage_raw"] = body.get("usage")
            record["model_returned"] = body.get("model")
            record["finish_raw"] = (body.get("choices") or [{}])[0].get("finish_reason")
        record["interesting_headers"] = {k: v for k, v in raw.headers.items() if k.startswith(("x-", "retry", "server-timing")) or "limit" in k}
    return record


def judge_schedule(text: str) -> dict:
    """What the app's own parser + validator make of a reply: the only quality measure that matters to the product."""
    try:
        entries, unreadable = parse_entries(text, DAY)
    except ParseError as exc:
        return {"parse": f"FAIL: {exc}"}
    valid, rejected = validate(entries, RULES, FIXED)
    return {"parse": "ok", "proposed": len(entries), "kept": len(valid), "rejected": [r["reason"] for r in rejected] + ["unreadable"] * len(unreadable),
            "blocks": [f"{e.start_at:%H:%M}-{e.end_at:%H:%M} {e.title}" for e in valid]}


def brief(r: dict) -> str:
    u = r.get("usage_raw") or {}
    details = u.get("completion_tokens_details") or {}
    line = f"{r['name']:<16} {r['model']:<22} {'ok ' if r.get('ok') else 'ERR'} {r['latency_s']:>6.1f}s  in={u.get('prompt_tokens')} out={u.get('completion_tokens')} total={u.get('total_tokens')} reasoning={details.get('reasoning_tokens')} finish={r.get('finish_raw') or r.get('finish_reason')}"
    if not r.get("ok"):
        line += f"\n    {r['error'][:300]}"
    elif "judged" in r:
        j = r["judged"]
        line += f"\n    parse={j.get('parse')} kept={j.get('kept')}/{j.get('proposed')} rejected={j.get('rejected')}"
    return line


async def list_models() -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": KEY}) as r:
            body = await r.json()
            print("HTTP", r.status)
    for m in body.get("models", []):
        if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]:
            print(f"  {m['name']:<42} in={m.get('inputTokenLimit')} out={m.get('outputTokenLimit')} thinking={m.get('thinking')}  {m.get('version', '')}")


async def main(args) -> int:
    if args.models:
        await list_models()
        return 0
    pool = storage.make_pool()
    await pool.open()
    try:
        cfg = load_config()
        used = await llm_routes.calls_today(pool)
        names = [n for n in args.run.split(",") if n]
        unknown = [n for n in names if n not in EXPERIMENTS]
        if unknown:
            print("unknown experiments:", unknown)
            return 2
        keep_free = args.keep_free
        if used + len(names) > cfg.llm.daily_call_cap - keep_free:
            print(f"refusing: {used} used + {len(names)} planned would leave fewer than {keep_free} of {cfg.llm.daily_call_cap} calls free today")
            return 2
        print(f"calls today {used}/{cfg.llm.daily_call_cap}; running {len(names)}: {names}\n")
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(names):
            record = await run_one(name, EXPERIMENTS[name], pool, args.timeout, cfg.llm.daily_call_cap)
            if record.get("ok") and name.startswith("sched"):
                record["judged"] = judge_schedule(record["text"])
            with RESULTS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            print(brief(record))
            if args.show_text and record.get("text"):
                print("    reply:", record["text"][:600].replace("\n", " "))
            if i < len(names) - 1:
                await asyncio.sleep(args.pause)            # stay well under any per-minute limit
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", default="", help="comma-separated experiment names")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--models", action="store_true", help="list the models the key can use (no generation)")
    parser.add_argument("--timeout", type=float, default=150.0, help="seconds to wait per call (the app uses 120)")
    parser.add_argument("--pause", type=float, default=4.0, help="seconds between calls")
    parser.add_argument("--keep-free", type=int, default=5, help="never use the last N calls of the day's cap")
    parser.add_argument("--show-text", action="store_true")
    ns = parser.parse_args()
    if ns.list:
        for k, v in EXPERIMENTS.items():
            print(f"{k:<16} {v['model']:<22} max_tokens={v['max_tokens']:<5} {v['extra'] or ''} {v['note']}")
        sys.exit(0)
    if not KEY:
        sys.exit("GEMINI_API_KEY is not set")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main(ns)))
