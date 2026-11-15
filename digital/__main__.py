"""Print the digital schedule of one day, read only:  python -m digital 2026-11-12 [--db]

Without --db the built-in seed rules are used; with --db the rules table is used (and must exist).
"""
from __future__ import annotations

import asyncio
import selectors
import sys
from datetime import date

from dotenv import load_dotenv

from digital.builder import IST, build_digital_schedule, unmapped
from digital.seed import SEED_RULES


def _t(iso: str) -> str:
    from datetime import datetime
    return datetime.fromisoformat(iso).astimezone(IST).strftime("%H:%M")


def render(sched: dict, unmapped_list: list[tuple[str, float]]) -> str:
    out = [f"DIGITAL SCHEDULE {sched['day']}  (05:00 to 05:00 IST)"]
    for track in ("laptop", "phone"):
        out.append(f"\n{track.upper()}")
        for b in sched[track]:
            flag = " late-night" if b["late_night"] else ""
            labels = " -> ".join(f"{l['label']} {l['minutes']:g}m" for l in b["labels"])
            out.append(f"  {_t(b['start'])}-{_t(b['end'])} {b['category']:<10} {b['minutes']:>5g}m{flag}  {labels}"[:260])
    out.append("\nTOTALS (minutes)")
    for track, cats in sched["totals"].items():
        out.append(f"  {track}: " + ", ".join(f"{c} {m:g}" for c, m in cats.items()))
    if unmapped_list:
        out.append("\nNO RULE YET (top by minutes)")
        out += [f"  {k}  {m:g}m" for k, m in unmapped_list]
    return "\n".join(out)


async def main(argv: list[str]) -> None:
    load_dotenv(".env")
    day = date.fromisoformat(argv[0])
    import storage
    from digital import store
    pool = storage.make_pool()
    await pool.open(wait=True, timeout=10)
    try:
        rules = await store.load_rules(pool) if "--db" in argv else SEED_RULES
        events = await store.load_events(pool, day)
    finally:
        await pool.close()
    print(render(build_digital_schedule(day, events, rules), unmapped(day, events, rules)))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(sys.argv[1:]), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
