# scripts/seed_synthetic.py — small, plausible, exactly-removable data for every table.
#
# Why this exists: every table but `skills` is empty or holds only real captured data, so no settings
# page, context compile or packer decision can be exercised during development (ASIDE_PLAN.md M5).
#
# It writes into the live DB with recent dates (user decision 2026-10-07), so two rules hold:
#   * every inserted id is recorded in data/seed_manifest.json, and --teardown deletes exactly those
#     ids and nothing else. There is no `zz` prefix because the content is meant to look real.
#   * schedule_snapshot and day_record are keyed by a unique `day`, and a recent day may already hold
#     a REAL row, so those two inserts are ON CONFLICT (day) DO NOTHING. A day the user actually
#     lived through is never overwritten.
#
# Run the teardown before measuring anything, before judging M2's "week of real data", and before
# recording latency/cost numbers for the resume (ASIDE_PLAN.md §7).
#
#   .venv/Scripts/python.exe scripts/seed_synthetic.py
#   .venv/Scripts/python.exe scripts/seed_synthetic.py --teardown

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from psycopg.types.json import Jsonb                      # noqa: E402

from context.items import Dropped, Item                    # noqa: E402
from context.recipe import load_recipe                      # noqa: E402
from llm.client import load_profile                          # noqa: E402
from storage import make_pool                                # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
MANIFEST = ROOT / "data" / "seed_manifest.json"

# Insert order. Teardown walks it backwards, which is what keeps the foreign keys happy:
# sessions.first_event_id -> events, events.session_id -> sessions, llm_trace/mark_wrong -> compile_log,
# persistent_entry.replaces_id -> persistent_entry.
ORDER = [
    "events", "sessions", "notes", "tasks", "task_log", "task_due_log",
    "schedule", "schedule_log", "schedule_snapshot", "day_record",
    "heartbeat_log", "monitoring_log", "persistent_entry", "diff_log",
    "compile_log", "llm_trace", "mark_wrong_log",
    "skills", "statements", "candidate_items", "instruction_records",
    "agent_runs", "eval_runs",
]


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


class Seeder:
    """Thin INSERT ... RETURNING id helper that remembers every id it created."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.manifest: dict[str, list[int]] = {}

    async def ins(self, table: str, cols: dict, *, conflict: str = "") -> int | None:
        names = ", ".join(cols)
        marks = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO {table} ({names}) VALUES ({marks}) {conflict} RETURNING id"
        async with self.conn.cursor() as cur:
            await cur.execute(sql, tuple(cols.values()))
            row = await cur.fetchone()
        if row is None:                      # ON CONFLICT DO NOTHING: a real row already owns that day
            return None
        self.manifest.setdefault(table, []).append(row[0])
        return row[0]


# --------------------------------------------------------------------------- the fictional week

def week(today: date) -> list[date]:
    return [today - timedelta(days=n) for n in range(6, -1, -1)]


async def seed_events_and_sessions(s: Seeder, days: list[date], gap_day: date) -> None:
    """Three events and two sessions a day. `gap_day` gets neither, which is the unexplained gap the
    Data gaps page should surface: the heartbeats below say the collector was healthy that day."""
    n = 0
    for day in days:
        if day == gap_day:
            continue
        slots = [
            ("Code.exe", "aside - server.py", 9, 0, 12, 30),
            ("chrome.exe", "localhost:8787 - Aside", 14, 0, 15, 0),
            ("chrome.exe", "Aside - settings", 20, 0, 22, 0),
        ]
        event_ids = []
        for app, title, h1, m1, h2, m2 in slots:
            n += 1
            event_ids.append(await s.ins("events", {
                "source": "probe", "kind": "focus", "app": app, "window_title": title,
                "domain": "localhost" if app == "chrome.exe" else None,
                "ts_start": at(day, h1, m1), "ts_end": at(day, h2, m2),
                "payload": Jsonb({"seed": True}), "dedupe_key": f"seed:ev:{n}",
            }))

        # Deep work, aligned with the 09:00-12:30 schedule block so planned-vs-actual means something.
        await s.ins("sessions", {
            "kind": "app", "app": "Code.exe", "started_at": at(day, 9, 5), "ended_at": at(day, 12, 20),
            "active_seconds": 10800.0, "event_count": 1, "first_event_id": event_ids[0],
        })
        # One idle stretch, so the review's "idle hidden" path has something to hide.
        await s.ins("sessions", {
            "kind": "idle", "app": None, "started_at": at(day, 13, 0), "ended_at": at(day, 14, 0),
            "active_seconds": 3600.0, "event_count": 1, "first_event_id": event_ids[1],
        })


async def seed_schedule(s: Seeder, days: list[date]) -> list[int]:
    """A weekday shape with a recurring morning block and a twice-weekly evening activity, so M7's
    pattern lifecycle (active after 3 occurrences) has something real to find."""
    ids: list[int] = []
    for day in days:
        entries: list[tuple[str, int, int, int, int, str, bool]] = []
        if day.weekday() < 5:                                   # Mon-Fri
            entries.append(("Gym", 7, 0, 8, 0, "user", False))
            entries.append(("Deep work — Aside", 9, 0, 12, 30, "user", False))
        else:
            entries.append(("Long walk", 8, 0, 9, 0, "user", False))
        if day.weekday() in (1, 4):                             # Tue, Fri
            entries.append(("Tennis with Rahul", 18, 30, 20, 0, "user", False))

        for title, h1, m1, h2, m2, origin, edited in entries:
            ids.append(await s.ins("schedule", {
                "title": title, "start_at": at(day, h1, m1), "end_at": at(day, h2, m2),
                "origin": origin, "edited_by_user": edited,
            }))

    # One generated-then-edited entry tomorrow, to exercise the provenance rules in §3.5.
    ids.append(await s.ins("schedule", {
        "title": "Review Aside M4 plan", "start_at": at(days[-1] + timedelta(days=1), 16, 0),
        "end_at": at(days[-1] + timedelta(days=1), 17, 0),
        "origin": "generated", "edited_by_user": True,
    }))
    return ids


async def seed_tasks(s: Seeder, today: date) -> None:
    """Five tasks, one of them slipped twice so the slip signal in §3.5 is visible."""
    specs = [
        ("Finish Aside M4 correctness pass", at(today + timedelta(days=2), 18, 0), "pending", None),
        ("Calibrate note similarity cutoff", at(today + timedelta(days=1), 12, 0), "pending", None),
        ("Book tennis court", at(today - timedelta(days=1), 9, 0), "done", at(today - timedelta(days=1), 8, 30)),
        ("Read pgvector HNSW docs", at(today + timedelta(days=3), 20, 0), "pending", None),
        ("Email Rahul about Friday", at(today - timedelta(days=3), 17, 0), "done", at(today - timedelta(days=3), 16, 10)),
    ]
    task_ids = []
    for text, due, status, completed in specs:
        task_ids.append(await s.ins("tasks", {
            "text": text, "due_at": due, "status": status,
            "created_at": at(today - timedelta(days=5), 10, 0), "completed_at": completed,
        }))

    for task_id, action in zip(task_ids, ["create", "create", "complete", "edit", "complete"]):
        await s.ins("task_log", {
            "task_id": task_id, "action": action, "snapshot": Jsonb({"seed": True, "action": action}),
        })

    slipped = task_ids[3]                                       # "Read pgvector HNSW docs", moved twice
    await s.ins("task_due_log", {"task_id": slipped, "old_due": at(today + timedelta(days=1), 20, 0),
                                 "new_due": at(today + timedelta(days=2), 20, 0)})
    await s.ins("task_due_log", {"task_id": slipped, "old_due": at(today + timedelta(days=2), 20, 0),
                                 "new_due": at(today + timedelta(days=3), 20, 0)})
    for task_id in task_ids[:3]:
        await s.ins("task_due_log", {"task_id": task_id, "old_due": None,
                                     "new_due": at(today + timedelta(days=2), 18, 0)})


async def seed_persistent(s: Seeder, today: date) -> None:
    """Five live entries plus one provisional replacement and one retired, so status filtering and the
    "Replaces" picker both have something to show. `source` is constrained to user/agent/note/pattern
    (ck_pe_source), which is why seed rows are tracked by the manifest rather than by a marker value."""
    created = at(today - timedelta(days=20), 9, 0)
    base = [
        ("identity", "Lives in Pune, India. All times are IST.", "user", 4, "active"),
        ("preferences", "Never schedule anything before 07:00.", "user", 3, "active"),
        ("routine", "Weekday mornings: gym 07:00-08:00, then deep work until 12:30.", "user", 5, "active"),
        ("goals", "Ship Aside v1 by December 2026.", "user", 2, "active"),
        ("people", "Rahul — tennis partner, plays Tuesday and Friday evenings.", "user", 3, "active"),
    ]
    ids = []
    for section, text, source, evidence, status in base:
        ids.append(await s.ins("persistent_entry", {
            "section": section, "text": text, "source": source, "created": created,
            "last_confirmed": at(today - timedelta(days=2), 9, 0), "evidence_count": evidence,
            "status": status, "seen_days": [today - timedelta(days=d) for d in (5, 3, 2)],
        }))

    # Provisional: seen on two days so far, so persistent_rules has not settled it yet (settle_days = 3).
    await s.ins("persistent_entry", {
        "section": "routine", "text": "Weekday mornings: swim 07:00-08:00, then deep work until 12:30.",
        "source": "note", "created": at(today - timedelta(days=2), 21, 0), "last_confirmed": None,
        "evidence_count": 2, "status": "provisional", "replaces_id": ids[2],
        "seen_days": [today - timedelta(days=2), today - timedelta(days=1)],
    })
    await s.ins("persistent_entry", {
        "section": "goals", "text": "Finish the labelling tool.", "source": "user",
        "created": at(today - timedelta(days=40), 9, 0), "last_confirmed": None,
        "evidence_count": 1, "status": "retired", "retired_reason": "superseded", "seen_days": [],
    })

    for n in range(5):
        await s.ins("diff_log", {
            "source_type": "persistent_entry", "source_id": str(ids[n % len(ids)]),
            "before": Jsonb({"entries": []}), "after": Jsonb({"entries": [{"text": base[n][1]}]}),
            "removed": Jsonb({}), "added": Jsonb({"entries": [{"text": base[n][1]}]}),
            "created_at": at(today - timedelta(days=5 - n % 5), 11, 0),
        })


async def seed_context_and_llm(s: Seeder, today: date) -> None:
    """Five compiles with real Item/Dropped shapes, so the Context viewer renders and a replay works.
    Built from the live classes rather than hand-written JSON, so the shape cannot drift."""
    recipe, profile = load_recipe("chat"), load_profile()
    queries = [
        "what does my week look like",
        "when am I playing tennis next",
        "summarise what I did today",
        "what is slipping",
        "draft a short email to Rahul",
    ]
    compile_ids = []
    for n, query in enumerate(queries):
        offered = [
            Item(id="persistent:1", text="Lives in Pune, India. All times are IST.",
                 source="persistent_file", priority=0, tokens=12, core=True),
            Item(id="persistent:3", text="Weekday mornings: gym 07:00-08:00, then deep work until 12:30.",
                 source="persistent_file", priority=-2, tokens=20, core=True),
            Item(id="skill:1", text="Keep emails under 5 sentences.", source="skills", priority=1, tokens=10),
            Item(id="schedule:1", text="Gym 07:00-08:00", source="schedule", priority=5, tokens=8),
            Item(id="note:2", text="Tennis moved to 18:30 from this week.", source="notes", priority=9, tokens=11),
            Item(id="persistent:7", text="Finish the labelling tool.", source="persistent_file",
                 priority=-3, tokens=8, status="retired"),
        ]
        dropped = [
            Dropped("persistent:7", "persistent_file", "filtered:retired", 8),
            Dropped("source:observations", "observations", "source_error", 0),
        ]
        chosen = [i for i in offered if i.status == "active"]
        compile_ids.append(await s.ins("compile_log", {
            "ts": at(today - timedelta(days=n), 19, 30), "situation": "chat",
            "recipe_name": recipe.name, "recipe_hash": recipe.hash, "model": profile.name,
            "offered": Jsonb([i.to_dict() for i in offered]),
            "chosen": Jsonb([{"id": i.id, "source": i.source, "tokens": i.tokens} for i in chosen]),
            "dropped": Jsonb([d.to_dict() for d in dropped]),
            "tokens": sum(i.tokens for i in chosen), "query": query,
        }))

    for n, compile_id in enumerate(compile_ids):
        await s.ins("llm_trace", {
            "ts": at(today - timedelta(days=n), 19, 30), "trace_id": f"{n:032x}", "span_id": f"{n:016x}",
            "parent_span_id": None, "operation": "chat", "provider": profile.provider, "model": profile.name,
            "input_tokens": 61, "output_tokens": 48 + n, "latency_ms": 820.0 + n * 40,
            "finish_reason": "stop", "error": None, "compile_log_id": compile_id,
            "attrs": Jsonb({"seed": True}),
        })

    # One replayable regression case, per §3.3: a mark points at the compile that was actually sent.
    await s.ins("mark_wrong_log", {
        "ts": at(today - timedelta(days=1), 19, 35), "target": "reply",
        "compile_log_id": compile_ids[1], "reason": "it gave Tuesday, tennis is Friday this week",
    })


async def seed_knowledge(s: Seeder, today: date) -> None:
    """Notes stay embedding_status = 'pending' on purpose: the real worker embeds them on next server
    start, which exercises the actual path instead of faking vectors."""
    notes = [
        ("Tennis moved to 18:30 from this week.", ["schedule"]),
        ("Felt scattered today — too many small context switches after lunch.", ["journal"]),
        ("pgvector HNSW needs ef_search tuning for filtered queries.", ["aside", "reading"]),
        ("Rahul can only do Friday this week.", ["people"]),
        ("Idea: show dropped context items in the viewer with their reason.", ["aside", "idea"]),
    ]
    for n, (text, tags) in enumerate(notes):
        await s.ins("notes", {
            "text": text, "tags": Jsonb(tags), "source": "seed",
            "created_at": at(today - timedelta(days=n), 21, 0), "embedding_status": "pending",
        })

    starter = json.loads((ROOT / "docs" / "skills.starter.json").read_text(encoding="utf-8"))
    for skill in starter[:5]:
        async with s.conn.cursor() as cur:        # seed_skills.py may already have loaded these; a duplicate wastes prompt tokens
            await cur.execute("SELECT 1 FROM skills WHERE name = %s", (skill["name"],))
            if await cur.fetchone():
                continue
        # The live table has use_when/affects (migration 061552b2195b), NOT tags/metadata_json, which is
        # what models_extra.py and storage/repo/skills.py assume. Seed the real columns, as seed_skills.py does.
        await s.ins("skills", {
            "name": skill["name"], "use_when": ", ".join(skill.get("tags", [])),
            "affects": "seed", "body": skill["content"], "usage_count": 0, "correction_count": 0,
        })

    statements = [
        ("Tomorrow is ekadashi, so I will skip lunch.", "journal"),
        ("Travelling on Thursday.", "note"),
        ("Client meeting at 6 AM on Friday.", "schedule"),
        ("I want to stop working after 22:00.", "journal"),
        ("Tennis is Friday this week, not Tuesday.", "note"),
    ]
    for n, (text, source) in enumerate(statements):
        await s.ins("statements", {"text": text, "source": source,
                                   "created_at": at(today - timedelta(days=n), 20, 0)})

    candidates = [
        ("constraint", "Skip lunch tomorrow (ekadashi).", "no lunch block", 0.9, "journal"),
        ("plan", "Travelling Thursday.", "tennis will not happen", 0.8, "note"),
        ("actual", "Client meeting 06:00 Friday.", "workout moves to evening", 0.95, "schedule"),
        ("preference", "Stop working after 22:00.", "no blocks after 22:00", 0.6, "journal"),
        ("prediction", "Tennis on Friday, not Tuesday.", "move the recurring block", 0.5, "note"),
    ]
    for n, (kind, text, effect, confidence, src) in enumerate(candidates):
        await s.ins("candidate_items", {
            "item_type": kind, "text": text, "effect": effect,
            "valid_from": today, "valid_until": today + timedelta(days=2),
            "confidence": confidence, "source_id": f"statement:{src}:{n}",
            "reason": "seeded example, not produced by an extractor",
        })

    for n, text in enumerate([
        "Put the gym before work tomorrow.",
        "Leave Friday evening free.",
        "No deep work blocks after 21:00.",
        "Keep Sunday empty.",
        "Schedule tennis with Rahul twice this week.",
    ]):
        await s.ins("instruction_records", {
            "text": text, "valid_from": today + timedelta(days=n % 3),
            "valid_until": today + timedelta(days=1 + n % 3),
        })


async def seed_health(s: Seeder, days: list[date], gap_day: date, pause_day: date) -> None:
    """Heartbeats stay healthy on `gap_day` so its missing sessions read as *unexplained*, while
    `pause_day` gets a monitoring pause/resume pair so that gap reads as *paused*. Two different
    reason paths on the Data gaps page."""
    for day in days:
        for hour in (9, 14, 20):
            await s.ins("heartbeat_log", {
                "ts": at(day, hour, 0), "collector_ok": True, "ingestor_ok": True,
            })

    await s.ins("monitoring_log", {"enabled": False, "created_at": at(pause_day, 13, 0)})
    await s.ins("monitoring_log", {"enabled": True, "created_at": at(pause_day, 16, 0)})
    for n, day in enumerate(days[:3]):
        await s.ins("monitoring_log", {"enabled": bool(n % 2), "created_at": at(day, 23, 0)})


async def seed_records(s: Seeder, days: list[date], schedule_ids: list[int]) -> None:
    """One snapshot and one day record per day. Both tables are keyed by a unique `day`, so a day the
    user really lived through keeps its real row and we simply get no id back."""
    for day in days:
        entries = [{"id": schedule_ids[0], "title": "Deep work — Aside",
                    "start_at": at(day, 9, 0).isoformat(), "end_at": at(day, 12, 30).isoformat(),
                    "origin": "user", "edited_by_user": False}]
        await s.ins("schedule_snapshot", {
            "day": day, "entries": Jsonb(entries), "late": False, "taken_at": at(day, 23, 55),
        }, conflict="ON CONFLICT (day) DO NOTHING")
        # `review` mirrors review/daily.py's shape loosely; nothing reads it until M7.
        await s.ins("day_record", {
            "day": day, "late": False, "created_at": at(day + timedelta(days=1), 0, 20),
            "data": Jsonb({
                "review": {"wall_clock_seconds": 14400, "planned_seconds": 16200,
                           "tasks_completed": 1, "notes": 1},
                "schedule": entries, "facts": [],
            }),
        }, conflict="ON CONFLICT (day) DO NOTHING")


async def seed_vestigial(s: Seeder, today: date) -> None:
    """agent_runs and eval_runs are unused but real tables; "every table" includes them."""
    for n in range(5):
        day = today - timedelta(days=n)
        await s.ins("agent_runs", {"status": "completed" if n else "running",
                                   "started_at": at(day, 13, 0),
                                   "completed_at": at(day, 13, 5) if n else None})
        await s.ins("eval_runs", {"status": "completed" if n else "failed",
                                  "started_at": at(day, 21, 0), "completed_at": at(day, 21, 2)})


# --------------------------------------------------------------------------- drivers

async def counts(conn) -> dict[str, int]:
    out = {}
    async with conn.cursor() as cur:
        for table in ORDER:
            await cur.execute(f"SELECT count(*) FROM {table}")
            out[table] = (await cur.fetchone())[0]
    return out


async def run_seed() -> None:
    pool = make_pool()
    await pool.open()
    try:
        if MANIFEST.exists():                      # idempotent: clear a previous seed first
            print("existing manifest found; tearing it down before re-seeding")
            await run_teardown(pool=pool)

        async with pool.connection() as conn:
            before = await counts(conn)
            today = datetime.now(IST).date()
            days = week(today)
            # Prefer a Sunday for the unexplained gap; otherwise the middle of the week.
            gap_day = next((d for d in days if d.weekday() == 6), days[3])
            pause_day = next((d for d in days if d not in (gap_day, days[-1])), days[2])

            s = Seeder(conn)
            await seed_events_and_sessions(s, days, gap_day)
            await seed_knowledge(s, today)
            await seed_tasks(s, today)
            schedule_ids = await seed_schedule(s, days)
            for n, schedule_id in enumerate(schedule_ids[:5]):
                action = ["create", "create", "edit", "edit", "delete"][n]
                await s.ins("schedule_log", {
                    "schedule_id": schedule_id, "action": action,
                    "before": None if action == "create" else Jsonb({"title": "Gym"}),
                    "after": None if action == "delete" else Jsonb({"title": "Gym", "seed": True}),
                    "created_at": at(today - timedelta(days=n), 8, 0),
                })
            await seed_records(s, days, schedule_ids)
            await seed_health(s, days, gap_day, pause_day)
            await seed_persistent(s, today)
            await seed_context_and_llm(s, today)
            await seed_vestigial(s, today)

            MANIFEST.parent.mkdir(parents=True, exist_ok=True)
            MANIFEST.write_text(json.dumps({
                "created_at": datetime.now(IST).isoformat(),
                "gap_day": gap_day.isoformat(), "pause_day": pause_day.isoformat(),
                "tables": s.manifest,
            }, indent=2), encoding="utf-8")

            after = await counts(conn)

        total = sum(len(v) for v in s.manifest.values())
        print(f"\nseeded {total} rows across {len(s.manifest)} tables")
        print(f"unexplained gap day: {gap_day}   paused day: {pause_day}")
        print(f"manifest: {MANIFEST}\n")
        for table in ORDER:
            delta = after[table] - before[table]
            if delta:
                print(f"  {table:22} {before[table]:>5} -> {after[table]:>5}  (+{delta})")
        skipped = [t for t in ("schedule_snapshot", "day_record")
                   if len(s.manifest.get(t, [])) < len(week(datetime.now(IST).date()))]
        if skipped:
            print(f"\n  note: real rows already owned some days in {', '.join(skipped)}; left untouched")
    finally:
        await pool.close()


async def run_teardown(pool=None) -> None:
    own_pool = pool is None
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST}; nothing to tear down")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))["tables"]
    if own_pool:
        pool = make_pool()
        await pool.open()
    try:
        async with pool.connection() as conn:
            removed = 0
            for table in reversed(ORDER):          # reverse insert order keeps the FKs happy
                ids = manifest.get(table) or []
                if not ids:
                    continue
                async with conn.cursor() as cur:
                    await cur.execute(f"DELETE FROM {table} WHERE id = ANY(%s)", (ids,))
                    print(f"  {table:22} -{cur.rowcount}")
                    removed += cur.rowcount
        MANIFEST.unlink()
        print(f"\nremoved {removed} rows; manifest deleted")
    finally:
        if own_pool:
            await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--teardown", action="store_true", help="delete exactly the rows in the manifest")
    args = parser.parse_args()
    if sys.platform == "win32":                    # psycopg async needs the selector loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_teardown() if args.teardown else run_seed())


if __name__ == "__main__":
    main()
