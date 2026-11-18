"""Slipping tasks: planned (or pushed) and still not done three days running get one question the next morning.

A day counts against a task when the final calendar had a block for it that day, or its due date was moved later that day.
The task is open now, so it was not done on any of those days. Days are the 05:00-to-05:00 days. Plain code, no model call.

The question is asked at most once per task per week, at most `MAX_PER_NIGHT` a night (most days slipped first), and is dropped
if nobody answers within three days. The phone shows it with its glow and answers it through the `answer_task_question` RPC.
A task parked as "someday" is never asked about again until it is revived.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row

from digital.builder import day_window

DAYS = 3                    # consecutive days before a task is asked about
LOOKBACK = 14               # days looked at, so the question can say how long it has really been
MAX_PER_NIGHT = 2           # never a wall of questions in one morning
ASK_AGAIN_AFTER = timedelta(days=7)
EXPIRES_AFTER = timedelta(days=3)


def streak(slip_days: set[date], last_day: date) -> int:
    """How many days in a row, ending at `last_day`, the task was planned or pushed."""
    n, d = 0, last_day
    while d in slip_days:
        n += 1
        d -= timedelta(days=1)
    return n


async def migrated(pool) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT to_regclass('public.task_question') IS NOT NULL, "
                              "EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'tasks' AND column_name = 'someday_at')")
            row = await cur.fetchone()
            return bool(row[0] and row[1])


async def _slip_days(cur, task_id: int, first: date, last: date) -> set[date]:
    lo, hi = day_window(first)[0], day_window(last)[1]
    await cur.execute(
        """SELECT d FROM (
               SELECT ((s.start_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date AS d
                 FROM schedule s WHERE s.task_id = %(t)s AND s.deleted_at IS NULL AND s.start_at >= %(lo)s AND s.start_at < %(hi)s
               UNION
               SELECT ((l.created_at AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date
                 FROM task_due_log l WHERE l.task_id = %(t)s AND l.old_due IS NOT NULL AND l.new_due > l.old_due
                  AND l.created_at >= %(lo)s AND l.created_at < %(hi)s) x""",
        {"t": task_id, "lo": lo, "hi": hi})
    return {r["d"] for r in await cur.fetchall()}


async def find_slipping(pool, last_day: date, now: datetime) -> list[dict[str, Any]]:
    """Open tasks that slipped `DAYS` days running up to `last_day` and were not asked about this week, worst first."""
    out = []
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT t.id, t.text FROM tasks t
                    WHERE t.status = 'pending' AND t.completed_at IS NULL AND t.dropped_at IS NULL AND t.someday_at IS NULL
                      AND NOT EXISTS (SELECT 1 FROM task_question q WHERE q.task_id = t.id AND q.created_at > %s)
                    ORDER BY t.id""", (now - ASK_AGAIN_AFTER,))
            for t in await cur.fetchall():
                n = streak(await _slip_days(cur, t["id"], last_day - timedelta(days=LOOKBACK - 1), last_day), last_day)
                if n >= DAYS:
                    out.append({"task_id": t["id"], "text": t["text"], "days": n})
    return sorted(out, key=lambda r: (-r["days"], r["task_id"]))


async def run_slipping_once(pool, last_day: date, for_day: date, now: datetime) -> dict[str, Any]:
    """Expire old unanswered questions, then ask about the worst slipping tasks. Safe to run twice: a task asked about is not asked again."""
    if not await migrated(pool):
        return {"skipped": "migration add_task_question is not applied"}
    async with pool.connection() as conn:
        cur = await conn.execute("UPDATE task_question SET status = 'expired' WHERE status = 'pending' AND created_at < %s", (now - EXPIRES_AFTER,))
        expired = cur.rowcount
    asked = []
    for t in (await find_slipping(pool, last_day, now))[:MAX_PER_NIGHT]:
        async with pool.connection() as conn:
            await conn.execute("INSERT INTO task_question (task_id, slipped_days, for_day, created_at) VALUES (%s, %s, %s, %s)",
                               (t["task_id"], t["days"], for_day, now))
        asked.append({"task_id": t["task_id"], "text": t["text"], "days": t["days"]})
    return {"asked": asked, "expired": expired}
