# scripts/seed_drafts.py — artificial schedule drafts, so the Drafts tab, the comparison and the reasons view have something to show.
#
#   .venv/Scripts/python.exe scripts/seed_drafts.py             # add them
#   .venv/Scripts/python.exe scripts/seed_drafts.py --teardown  # remove exactly what it added
#
# What it adds (all plausible, none real): two past weekdays more than 9 days back, each with a draft history and the schedule
# rows that came out of it:
#   day A  version 1 (model): 4 proposed blocks, 2 blocks the checks dropped; you accepted one, edited one, discarded one;
#          then asked for a revision -> version 2 (model): everything accepted. You also added a dentist appointment yourself.
#   day B  one rule-based draft (copied from the previous week), all accepted, plus a call you added yourself.
# Days are chosen older than 9 days so the nightly catch-up job (which looks back 7 days) can never treat them as real days, and
# only days that have no schedule rows, snapshot or draft already. Every id is written to data/seed_drafts_manifest.json and
# teardown deletes exactly those rows. It writes no tasks, notes, persistent entries or anything the model would read.
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

import storage  # noqa: E402
from schedule_gen.model import ACCEPTED, DISCARDED, IST, PENDING, DraftEntry  # noqa: E402

MANIFEST = ROOT / "data" / "seed_drafts_manifest.json"


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), IST)


def entry(day, title, h1, m1, h2, m2, reason, **kw) -> DraftEntry:
    return DraftEntry(title=title, start_at=at(day, h1, m1), end_at=at(day, h2, m2), reason=reason, **kw)


def rejected(day, title, h1, m1, h2, m2, why):
    return {"entry": entry(day, title, h1, m1, h2, m2, "").to_json(), "reason": why}


async def free_days(pool, count: int) -> list[date]:
    """The newest weekdays, 9+ days back, that have nothing on them yet."""
    found, day = [], date.today() - timedelta(days=9)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            while len(found) < count and day > date.today() - timedelta(days=90):
                if day.weekday() < 5:
                    start = at(day, 0)
                    await cur.execute("SELECT (SELECT count(*) FROM schedule WHERE start_at >= %s AND start_at < %s)"
                                      " + (SELECT count(*) FROM schedule_snapshot WHERE day = %s)"
                                      " + (SELECT count(*) FROM schedule_draft WHERE target_day = %s)", (start, start + timedelta(days=1), day, day))
                    if (await cur.fetchone())[0] == 0:
                        found.append(day)
                day -= timedelta(days=1)
    if len(found) < count:
        raise SystemExit("could not find enough empty weekdays to seed")
    return found


async def seed() -> None:
    if MANIFEST.exists():
        print("already seeded (data/seed_drafts_manifest.json exists); run with --teardown first")
        return
    pool = storage.make_pool()
    await pool.open()
    made = {"schedule": [], "schedule_draft": [], "instruction_records": []}
    try:
        day_a, day_b = await free_days(pool, 2)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:

                async def add_row(day, title, h1, m1, h2, m2, origin, edited=False) -> int:
                    await cur.execute("INSERT INTO schedule (title, start_at, end_at, origin, edited_by_user) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                                      (title, at(day, h1, m1), at(day, h2, m2), origin, edited))
                    made["schedule"].append((await cur.fetchone())[0])
                    return made["schedule"][-1]

                async def add_draft(day, version, parent, status, source, model, instruction, proposed, working, rej, inputs, created) -> int:
                    await cur.execute(
                        """INSERT INTO schedule_draft (target_day, version, parent_id, status, source, model, instruction, proposed, entries, rejected,
                                                       inputs, created_at, resolved_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (day, version, parent, status, source, model, instruction, Jsonb([e.to_json() for e in proposed]),
                         Jsonb([e.to_json() for e in working]), Jsonb(rej), Jsonb(inputs), created, created + timedelta(minutes=9) if status != "open" else None))
                    made["schedule_draft"].append((await cur.fetchone())[0])
                    return made["schedule_draft"][-1]

                async def add_instruction(day, text):
                    await cur.execute("INSERT INTO instruction_records (text, valid_from, valid_until, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                                      (text, day, day, at(day - timedelta(days=1), 21, 5)))
                    made["instruction_records"].append((await cur.fetchone())[0])

                # ---------------- day A: a model draft, edited, revised ----------------
                a = day_a
                gym, deep, errands, run = (
                    ("Gym", 7, 0, 8, 0, "your usual weekday routine"),
                    ("Deep work", 9, 0, 12, 30, "your usual weekday focus block"),
                    ("Errands", 15, 0, 16, 0, "you have an errand task due today"),
                    ("Run", 18, 30, 19, 15, "you asked to keep the evening free for a run"),
                )
                proposed1 = [entry(a, t, h1, m1, h2, m2, r) for (t, h1, m1, h2, m2, r) in (gym, deep, errands, run)]
                gym_id = await add_row(a, "Gym", 7, 0, 8, 0, "generated")
                deep_id = await add_row(a, "Deep work", 9, 30, 13, 0, "generated", edited=True)
                run_id = await add_row(a, "Run", 17, 30, 18, 15, "generated")
                await add_row(a, "Dentist", 15, 0, 15, 30, "user")

                working1 = [
                    entry(a, "Gym", 7, 0, 8, 0, gym[5], state=ACCEPTED, locked=True, schedule_id=gym_id),
                    entry(a, "Deep work", 9, 30, 13, 0, deep[5], state=PENDING, locked=True, edited=True),
                    entry(a, "Errands", 15, 0, 16, 0, errands[5], state=DISCARDED),
                    entry(a, "Run", 18, 30, 19, 15, run[5], state=PENDING),
                ]
                rej1 = [rejected(a, "Late reading", 23, 30, 23, 45, "outside_waking_hours"), rejected(a, "Stretch break", 12, 40, 12, 45, "too_short")]
                created1 = at(a - timedelta(days=1), 21, 10)
                await add_instruction(a, "keep the evening free for a run")
                v1 = await add_draft(a, 1, None, "superseded", "llm", "gemini-3.6-flash", "keep the evening free for a run", proposed1, working1, rej1,
                                     {"day": a.isoformat(), "template_day": (a - timedelta(days=7)).isoformat(), "template_count": 2, "instruction_count": 1,
                                      "candidate_ids": [], "task_ids": [], "locked_count": 0, "current_count": 0, "free_slots": 3}, created1)

                proposed2 = [
                    entry(a, "Gym", 7, 0, 8, 0, gym[5], state=ACCEPTED, locked=True, schedule_id=gym_id),
                    entry(a, "Deep work", 9, 30, 13, 0, deep[5], state=PENDING, locked=True, edited=True),
                    entry(a, "Run", 17, 30, 18, 15, "moved earlier, as you asked"),
                ]
                working2 = [
                    entry(a, "Gym", 7, 0, 8, 0, gym[5], state=ACCEPTED, locked=True, schedule_id=gym_id),
                    entry(a, "Deep work", 9, 30, 13, 0, deep[5], state=ACCEPTED, locked=True, edited=True, schedule_id=deep_id),
                    entry(a, "Run", 17, 30, 18, 15, "moved earlier, as you asked", state=ACCEPTED, locked=True, schedule_id=run_id),
                ]
                created2 = at(a - timedelta(days=1), 21, 24)
                await add_instruction(a, "move the run earlier and skip errands")
                await add_draft(a, 2, v1, "accepted", "llm", "gemini-3.6-flash", "move the run earlier and skip errands", proposed2, working2, [],
                                {"day": a.isoformat(), "instruction_count": 2, "locked_count": 1, "current_count": 1, "free_slots": 4, "revises": v1}, created2)

                # ---------------- day B: a rule-based draft, accepted as it was ----------------
                b = day_b
                g2 = await add_row(b, "Gym", 7, 0, 8, 0, "generated")
                d2 = await add_row(b, "Deep work", 9, 0, 12, 0, "generated")
                await add_row(b, "Call with Rahul", 16, 0, 16, 30, "user")
                copied = f"Copied from {(b - timedelta(days=7)):%a %d %b}"
                proposed_b = [entry(b, "Gym", 7, 0, 8, 0, copied), entry(b, "Deep work", 9, 0, 12, 0, copied)]
                working_b = [entry(b, "Gym", 7, 0, 8, 0, copied, state=ACCEPTED, locked=True, schedule_id=g2),
                             entry(b, "Deep work", 9, 0, 12, 0, copied, state=ACCEPTED, locked=True, schedule_id=d2)]
                await add_draft(b, 1, None, "accepted", "placeholder", None, "", proposed_b, working_b, [],
                                {"day": b.isoformat(), "template_day": (b - timedelta(days=7)).isoformat(), "template_count": 2}, at(b - timedelta(days=1), 20, 40))
    finally:
        await pool.close()

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({"created_at": datetime.now(IST).isoformat(), "days": [day_a.isoformat(), day_b.isoformat()], **made}, indent=2), encoding="utf-8")
    print(f"seeded drafts for {day_a} and {day_b}: {len(made['schedule_draft'])} draft versions, {len(made['schedule'])} schedule rows, "
          f"{len(made['instruction_records'])} instructions")
    print("remove with: .venv/Scripts/python.exe scripts/seed_drafts.py --teardown")


async def teardown() -> None:
    if not MANIFEST.exists():
        print("nothing to tear down")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pool = storage.make_pool()
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # newest version first: version 2 points at version 1 (ON DELETE SET NULL would also cope, this is just tidy)
                await cur.execute("DELETE FROM schedule_draft WHERE id = ANY(%s)", (manifest["schedule_draft"],))
                print(f"  schedule_draft       -{cur.rowcount}")
                await cur.execute("DELETE FROM schedule_log WHERE schedule_id = ANY(%s)", (manifest["schedule"],))
                await cur.execute("DELETE FROM schedule WHERE id = ANY(%s)", (manifest["schedule"],))
                print(f"  schedule             -{cur.rowcount}")
                await cur.execute("DELETE FROM instruction_records WHERE id = ANY(%s)", (manifest["instruction_records"],))
                print(f"  instruction_records  -{cur.rowcount}")
    finally:
        await pool.close()
    MANIFEST.unlink()
    print("removed; manifest deleted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--teardown", action="store_true")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(teardown() if args.teardown else seed())
