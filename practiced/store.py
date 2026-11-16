"""Database side of the practiced schedule. Reads are plain selects; the only writes are `practiced_day` and `title_tag`."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from digital import store as digital_store
from digital.builder import IST, build_digital_schedule, day_window
from practiced.builder import Plan, Tag, build_practiced_schedule
from practiced.tags import request_tags


async def load_plans(pool, day: date) -> list[Plan]:
    """Final calendar blocks that start inside the 05:00-to-05:00 day."""
    lo, hi = day_window(day)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, title, start_at, end_at, origin, edited_by_user FROM schedule "
                "WHERE deleted_at IS NULL AND start_at >= %s AND start_at < %s ORDER BY start_at", (lo, hi))
            return [Plan(r["id"], r["title"], r["start_at"].astimezone(IST), r["end_at"].astimezone(IST), r["origin"], r["edited_by_user"])
                    for r in await cur.fetchall()]


async def load_tasks(pool, day: date) -> tuple[list[dict], list[dict]]:
    """(done that day, still open and slipping)."""
    lo, hi = day_window(day)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT text, completed_at FROM tasks WHERE completed_at >= %s AND completed_at < %s ORDER BY completed_at", (lo, hi))
            done = [{"text": r["text"], "at": r["completed_at"].astimezone(IST).strftime("%H:%M")} for r in await cur.fetchall()]
            await cur.execute(
                "SELECT text, slip_count, due_at FROM tasks WHERE completed_at IS NULL AND dropped_at IS NULL AND slip_count > 0 "
                "ORDER BY slip_count DESC, id")
            slipping = [{"text": r["text"], "slip_count": r["slip_count"],
                         "due": r["due_at"].astimezone(IST).date().isoformat() if r["due_at"] else None} for r in await cur.fetchall()]
    return done, slipping


async def load_answers(pool, day: date) -> tuple[list[dict], str | None]:
    """(the day's phone answers, the day's context label if the person gave one). Tolerates a database without the table."""
    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT kind, answered_at, payload FROM phone_answer WHERE day = %s AND kind IN ('distraction', 'sleep', 'evening') "
                                  "ORDER BY answered_at", (day,))
                answers = [{**r, "answered_at": r["answered_at"].astimezone(IST)} for r in await cur.fetchall()]
                await cur.execute("SELECT payload->>'label' AS label FROM phone_answer WHERE kind = 'anomaly' AND payload->>'for_day' = %s "
                                  "ORDER BY answered_at DESC LIMIT 1", (day.isoformat(),))
                row = await cur.fetchone()
        return answers, (row["label"] if row else None)
    except Exception:         # the migration is not on this database yet
        return [], None


async def load_draft(pool, day: date) -> dict | None:
    """The newest generated draft for the day that was not thrown away whole."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT proposed, entries FROM schedule_draft WHERE target_day = %s AND status <> 'discarded' "
                "ORDER BY version DESC LIMIT 1", (day,))
            return await cur.fetchone()


async def load_tags(pool, titles: list[str]) -> dict[str, Tag]:
    if not titles:
        return {}
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT title, expected, observable FROM title_tag WHERE title = ANY(%s)", (titles,))
            return {r["title"]: Tag(tuple(r["expected"]), r["observable"]) for r in await cur.fetchall()}


async def save_tags(pool, tags: dict[str, Tag], origin: str = "llm") -> None:
    """Never overwrites a tag the user set."""
    async with pool.connection() as conn:
        for title, t in tags.items():
            await conn.execute(
                "INSERT INTO title_tag (title, expected, observable, origin) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (title) DO UPDATE SET expected = EXCLUDED.expected, observable = EXCLUDED.observable, origin = EXCLUDED.origin "
                "WHERE title_tag.origin <> 'user'", (title, list(t.expected), t.observable, origin))


async def ensure_tags(pool, titles: list[str], client) -> dict[str, Tag]:
    """Tags for these titles; one model call for the ones not cached yet. Without a client, only what is cached."""
    tags = await load_tags(pool, titles)
    missing = [t for t in dict.fromkeys(titles) if t not in tags]
    if missing and client is not None:
        new = await request_tags(client, missing)
        await save_tags(pool, new)
        tags.update(new)
    return tags


async def build_day(pool, day: date, *, client=None) -> dict[str, Any]:
    """Everything for one day, from the database. Seeds the rules table on first use."""
    await digital_store.seed_if_empty(pool)
    digital = build_digital_schedule(day, await digital_store.load_events(pool, day), await digital_store.load_rules(pool))
    plans = await load_plans(pool, day)
    tags = await ensure_tags(pool, [p.title for p in plans], client)
    done, slipping = await load_tasks(pool, day)
    answers, context = await load_answers(pool, day)
    return build_practiced_schedule(digital, plans, tags, tasks_done=done, tasks_slipping=slipping, draft=await load_draft(pool, day),
                                    answers=answers, context=context)


async def save_practiced(pool, day: date, data: dict) -> None:
    """Rebuilding a day replaces its schedule and clears the digest, which no longer matches it."""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO practiced_day (day, data) VALUES (%s, %s) "
            "ON CONFLICT (day) DO UPDATE SET data = EXCLUDED.data, built_at = now(), "
            "digest = CASE WHEN practiced_day.data = EXCLUDED.data THEN practiced_day.digest ELSE NULL END, "
            "digest_at = CASE WHEN practiced_day.data = EXCLUDED.data THEN practiced_day.digest_at ELSE NULL END",
            (day, Jsonb(data)))


async def get_practiced(pool, day: date) -> dict | None:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT data, digest FROM practiced_day WHERE day = %s", (day,))
            return await cur.fetchone()


async def save_digest(pool, day: date, digest: dict) -> None:
    async with pool.connection() as conn:
        await conn.execute("UPDATE practiced_day SET digest = %s, digest_at = now() WHERE day = %s", (Jsonb(digest), day))
    if digest.get("title_tags"):
        await storage.save_suggested_rules(pool, digest["title_tags"])


def finished_days(now: datetime, count: int) -> list[date]:
    """The last `count` logical days (05:00 to 05:00 IST) that are over, oldest first."""
    today_start = now.astimezone(IST).replace(hour=5, minute=0, second=0, microsecond=0)
    last_over = today_start.date() - timedelta(days=1) if now.astimezone(IST) >= today_start else today_start.date() - timedelta(days=2)
    return [last_over - timedelta(days=n) for n in range(count - 1, -1, -1)]


def has_signal(practiced: dict) -> bool:
    return bool(practiced["plan"]) or any(practiced["totals"][t] for t in ("laptop", "phone"))


async def run_digest_once(pool, client, now: datetime, *, catchup: int = 3) -> dict[str, list[str]]:
    """For each recently finished day: rebuild its practiced schedule (a changed calendar or late phone upload clears the
    old digest) and write the digest if there is none. Days with no data at all are skipped. Raises on the first model
    failure so the nightly step is recorded as failed; days already done stay done."""
    from practiced.digest import generate_digest      # imported here: the digest prompt is only needed when a call is made

    done: dict[str, list[str]] = {"built": [], "digested": [], "empty": []}
    for day in finished_days(now, catchup):
        practiced = await build_day(pool, day, client=client)
        if not has_signal(practiced):
            done["empty"].append(day.isoformat())
            continue
        await save_practiced(pool, day, practiced)
        done["built"].append(day.isoformat())
        row = await get_practiced(pool, day)
        if row["digest"] is None:
            await save_digest(pool, day, await generate_digest(client, row["data"]))
            done["digested"].append(day.isoformat())
    return done


async def load_history(pool, today: date, days: int = 90) -> list[dict]:
    """Stored practiced days before `today`, oldest first (the data patterns are computed from)."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT data FROM practiced_day WHERE day < %s AND day >= %s ORDER BY day", (today, today - timedelta(days=days)))
            return [r["data"] for r in await cur.fetchall()]


async def backfill(pool, client, now: datetime, days: int = 30) -> dict[str, list[str]]:
    """Build and store the practiced schedule (no digest) of each finished day that has none yet. One tag call per batch of
    new calendar titles, no more. Meant to be run by hand once, so patterns have history to learn from."""
    have = {r["day"] for r in await _stored_days(pool)}
    built, empty = [], []
    for day in finished_days(now, days):
        if day in have:
            continue
        practiced = await build_day(pool, day, client=client)
        if has_signal(practiced):
            await save_practiced(pool, day, practiced)
            built.append(day.isoformat())
        else:
            empty.append(day.isoformat())
    return {"built": built, "empty": empty}


async def _stored_days(pool) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT day FROM practiced_day")
            return await cur.fetchall()
