"""Print the practiced schedule of one day, read only: python -m practiced YYYY-MM-DD [--db]

Needs no model: titles without a cached tag show as UNTAGGED. --db reads DATABASE_URL (live, read only); otherwise the test database."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv(".env")
USE_LIVE = "--db" in sys.argv
if not USE_LIVE and os.environ.get("DATABASE_URL_TEST"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_TEST"].strip()

import storage  # noqa: E402
from digital import store as digital_store  # noqa: E402
from digital.builder import build_digital_schedule  # noqa: E402
from digital.seed import SEED_RULES  # noqa: E402
from practiced.builder import build_practiced_schedule  # noqa: E402
from practiced.store import load_answers, load_draft, load_plans, load_tags, load_tasks  # noqa: E402


def show(r: dict) -> None:
    print(f"== {r['day']} ==")
    for p in r["plan"]:
        print(f"{p['start']}-{p['end']}  {p['title']}  [{p['source']}]  -> {p['verdict']}")
        print(f"   planned {p['planned_min']}m | on-plan {p['on_plan_min']}m ({p['on_plan_pct']}%) | idle {p['idle_min']}m | phone in window {p['phone_min']}m")
        for a in p["activities"]:
            print(f"   {a['track']:6} {a['start']}-{a['end']} {a['category']:10} " + " -> ".join(f"{l['label']} {l['minutes']:g}m" for l in a["labels"][:4]))
    print("OUTSIDE THE PLAN (>=30m, non-idle)")
    for a in r["outside"]:
        print(f"   {a['track']:6} {a['start']}-{a['end']} {a['minutes']:g}m {a['category']}  " + " -> ".join(f"{l['label']} {l['minutes']:g}m" for l in a["labels"][:4]))
    print("TOTALS", r["totals"])
    print("TASKS", r["tasks"])
    print("PLAN CHANGES", r["plan_changes"])


async def main() -> None:
    day = date.fromisoformat(sys.argv[1])
    pool = storage.make_pool()
    await pool.open(wait=True, timeout=10)
    try:
        try:
            rules = await digital_store.load_rules(pool) or SEED_RULES
        except Exception:      # the migration is not on this database yet
            rules = SEED_RULES
        digital = build_digital_schedule(day, await digital_store.load_events(pool, day), rules)
        plans = await load_plans(pool, day)
        try:
            tags = await load_tags(pool, [p.title for p in plans])
        except Exception:
            tags = {}
        done, slipping = await load_tasks(pool, day)
        answers, context = await load_answers(pool, day)
        show(build_practiced_schedule(digital, plans, tags, tasks_done=done, tasks_slipping=slipping, draft=await load_draft(pool, day),
                                      answers=answers, context=context))
    finally:
        await pool.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
