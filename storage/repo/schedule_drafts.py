# storage/repo/schedule_drafts.py — versions of a generated schedule (table schedule_draft, plan 3.6).
# Entries travel as plain dicts (DraftEntry.to_json); this module never needs to understand them beyond state/times.
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from schedule_gen.model import ACCEPTED, DISCARDED, PENDING, DraftEntry, settled_status
from storage.repo.schedule import CREATE_SCHEDULE_SQL, _log, _row_to_dict

COLUMNS = ("id, target_day, version, parent_id, status, source, model, instruction, proposed, entries, rejected, inputs, "
           "compile_log_id, created_at, resolved_at")
KEYS = [c.strip() for c in COLUMNS.split(",")]


def _row(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(KEYS, row))


async def insert_draft(
    pool, *, target_day: date, source: str, model: str | None, instruction: str, entries: list[dict[str, Any]],
    rejected: list[dict[str, Any]], inputs: dict[str, Any], compile_log_id: int | None = None,
) -> dict[str, Any]:
    """A new OPEN version for `target_day`. The previous open version (if any) becomes `superseded` in the same
    transaction and stays on file; version 1 is therefore always the first draft ever made for that day. `proposed`
    is written once, here, as a copy of `entries`, and no function in this module ever changes it."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT id FROM schedule_draft WHERE target_day = %s AND status = 'open'", (target_day,))
            open_row = await cur.fetchone()
            await cur.execute("SELECT coalesce(max(version), 0) FROM schedule_draft WHERE target_day = %s", (target_day,))
            version = (await cur.fetchone())[0] + 1
            parent_id = open_row[0] if open_row else None
            if parent_id is not None:
                await cur.execute("UPDATE schedule_draft SET status = 'superseded', resolved_at = now() WHERE id = %s", (parent_id,))
            await cur.execute(
                f"""INSERT INTO schedule_draft (target_day, version, parent_id, status, source, model, instruction, proposed,
                                                entries, rejected, inputs, compile_log_id)
                    VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}""",
                (target_day, version, parent_id, source, model, instruction, Jsonb(entries), Jsonb(entries), Jsonb(rejected),
                 Jsonb(inputs), compile_log_id),
            )
            return _row(await cur.fetchone())


async def get_draft(pool, draft_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM schedule_draft WHERE id = %s", (draft_id,))
            row = await cur.fetchone()
    return _row(row) if row else None


async def get_open_draft(pool, target_day: date) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM schedule_draft WHERE target_day = %s AND status = 'open'", (target_day,))
            row = await cur.fetchone()
    return _row(row) if row else None


async def list_drafts(pool, *, day: date | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. With `day`, every version made for that day (version 1 is the first generated draft)."""
    where, params = ("WHERE target_day = %s", (day, limit)) if day else ("", (limit,))
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM schedule_draft {where} ORDER BY target_day DESC, version DESC LIMIT %s", params)
            rows = await cur.fetchall()
    return [_row(r) for r in rows]


async def save_entries(pool, draft_id: int, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Replace the entries of an OPEN draft and close it if nothing is left pending. None if it is not open."""
    status = settled_status([DraftEntry.from_json(e) for e in entries])
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""UPDATE schedule_draft
                    SET entries = %s, status = coalesce(%s::text, status), resolved_at = CASE WHEN %s::text IS NULL THEN resolved_at ELSE now() END
                    WHERE id = %s AND status = 'open' RETURNING {COLUMNS}""",
                (Jsonb(entries), status, status, draft_id),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def discard_draft(pool, draft_id: int) -> dict[str, Any] | None:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE schedule_draft SET status = 'discarded', resolved_at = now() WHERE id = %s AND status = 'open' RETURNING {COLUMNS}",
                (draft_id,),
            )
            row = await cur.fetchone()
    return _row(row) if row else None


async def accept_entries(pool, draft_id: int, indexes: list[int]) -> dict[str, Any] | None:
    """Turn the chosen pending entries into schedule rows (origin `generated`, logged like any create) and mark them
    accepted + locked, all in ONE transaction. An entry that now overlaps something added since the draft was made is
    left pending and reported in `conflicts`. Returns {draft, accepted: [schedule ids], conflicts: [indexes]}, or None
    if the draft is not open."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM schedule_draft WHERE id = %s FOR UPDATE", (draft_id,))
            row = await cur.fetchone()
            if row is None or row[4] != "open":
                return None
            draft = _row(row)
            entries = [DraftEntry.from_json(e) for e in draft["entries"]]

            accepted, conflicts = [], []
            for index in indexes:
                if not 0 <= index < len(entries) or entries[index].state != PENDING:
                    continue
                e = entries[index]
                await cur.execute("SELECT 1 FROM schedule WHERE start_at < %s AND end_at > %s LIMIT 1", (e.end_at, e.start_at))
                if await cur.fetchone():
                    conflicts.append(index)
                    continue
                await cur.execute(CREATE_SCHEDULE_SQL, (e.title, e.start_at, e.end_at, "generated"))
                created = _row_to_dict(await cur.fetchone())
                await _log(cur, created["id"], "create", None, created)
                e.state, e.locked, e.schedule_id = ACCEPTED, True, created["id"]
                accepted.append(created["id"])

            status = settled_status(entries)
            payload = [e.to_json() for e in entries]
            await cur.execute(
                f"""UPDATE schedule_draft
                    SET entries = %s, status = coalesce(%s::text, status), resolved_at = CASE WHEN %s::text IS NULL THEN resolved_at ELSE now() END
                    WHERE id = %s RETURNING {COLUMNS}""",
                (Jsonb(payload), status, status, draft_id),
            )
            draft = _row(await cur.fetchone())
    return {"draft": draft, "accepted": accepted, "conflicts": conflicts}
