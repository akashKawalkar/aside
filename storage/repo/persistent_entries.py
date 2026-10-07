# storage/repo/persistent_entries.py — the persistent file: one row per entry, every change logged in diff_log, undoable.
# Rules (pure functions) are in context/persistent_rules.py; the shape is context/persistent_schema.py.
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config import Memory
from context.persistent_rules import ist_day, is_settled, pick_evictions, with_day
from context.persistent_schema import LIVE_STATUSES, SECTIONS, Entry
from llm.tokens import estimate_tokens

COLUMNS = "id, section, text, source, created, last_confirmed, evidence_count, status, retired_reason, replaces_id, seen_days"
TRACKED = ("section", "text", "source", "created", "last_confirmed", "evidence_count", "status", "retired_reason", "replaces_id", "seen_days")
ACTIONS = {"add", "edit", "confirm", "retire", "evict"}   # diff_log source_types that can be undone
SECTION_ORDER = {name: i for i, name in enumerate(SECTIONS)}


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def ser(row: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of an entry row, stable enough to compare two states of the same entry."""
    return {
        "id": row["id"],
        **{k: row[k] for k in ("section", "text", "source", "evidence_count", "status", "retired_reason", "replaces_id")},
        "created": _utc(row["created"]),
        "last_confirmed": _utc(row["last_confirmed"]) if row["last_confirmed"] else None,
        "seen_days": [d.isoformat() for d in row["seen_days"]],
    }


def _restore_values(state: dict[str, Any]) -> dict[str, Any]:
    out = {k: state[k] for k in TRACKED}
    out["created"] = datetime.fromisoformat(state["created"])
    out["last_confirmed"] = datetime.fromisoformat(state["last_confirmed"]) if state["last_confirmed"] else None
    out["seen_days"] = [date.fromisoformat(d) for d in state["seen_days"]]
    return out


def _line(state: dict[str, Any]) -> str:
    return f"[{state['section']}] {state['text']} ({state['status']}, seen {state['evidence_count']}x on {len(state['seen_days'])} days)"


async def _get(cur, entry_id: int) -> dict[str, Any] | None:
    await cur.execute(f"SELECT {COLUMNS} FROM persistent_entry WHERE id = %s FOR UPDATE", (entry_id,))
    return await cur.fetchone()


async def _all(cur) -> list[dict[str, Any]]:
    await cur.execute(f"SELECT {COLUMNS} FROM persistent_entry")
    return await cur.fetchall()


async def _set(cur, entry_id: int, **fields) -> dict[str, Any]:
    names = list(fields)
    await cur.execute(
        f"UPDATE persistent_entry SET {', '.join(f'{n} = %s' for n in names)} WHERE id = %s RETURNING {COLUMNS}",
        [*fields.values(), entry_id],
    )
    return await cur.fetchone()


async def _log(cur, action: str, source_id: str, before: list[dict], after: list[dict]) -> int:
    """One diff_log row per change; before/after are {"entries": [...]} so one row can cover several entries."""
    await cur.execute(
        "INSERT INTO diff_log (source_type, source_id, before, after, removed, added) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (action, source_id, Jsonb({"entries": before}), Jsonb({"entries": after}),
         Jsonb([_line(s) for s in before]), Jsonb([_line(s) for s in after])),
    )
    return (await cur.fetchone())["id"]


async def _evict_to_cap(cur, memory: Memory, keep: frozenset[int]) -> list[dict]:
    picks = pick_evictions(await _all(cur), memory.cap_tokens, memory.evict_first, estimate_tokens, keep)
    evicted = []
    for e in picks:
        new = await _set(cur, e["id"], status="retired", retired_reason="evicted")
        await _log(cur, "evict", str(e["id"]), [ser(e)], [ser(new)])
        evicted.append(ser(new))
    return evicted


async def _confirm(cur, e: dict[str, Any], now: datetime, memory: Memory) -> tuple[dict, list[dict]]:
    """Count one more sighting. A provisional entry seen on enough separate days becomes active and retires what it replaced."""
    if e["status"] not in LIVE_STATUSES:
        raise ValueError("That entry is retired.")
    seen = with_day(e["seen_days"], now)
    fields: dict[str, Any] = {"seen_days": seen, "evidence_count": e["evidence_count"] + 1,
                              "last_confirmed": max(now, e["created"])}
    if e["status"] == "provisional" and is_settled(seen, memory.settle_days):
        fields["status"] = "active"
    new = await _set(cur, e["id"], **fields)
    before, after = [ser(e)], [ser(new)]
    if e["status"] == "provisional" and new["status"] == "active" and e["replaces_id"]:
        old = await _get(cur, e["replaces_id"])
        if old and old["status"] in LIVE_STATUSES:
            retired = await _set(cur, old["id"], status="retired", retired_reason="replaced")
            before.append(ser(old))
            after.append(ser(retired))
    await _log(cur, "confirm", str(e["id"]), before, after)
    return after[0], after[1:]


async def list_persistent_entries(pool, *, include_retired: bool = True) -> list[dict[str, Any]]:
    """Entries in section order, live ones first, oldest first."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            where = "" if include_retired else "WHERE status <> 'retired'"
            await cur.execute(f"SELECT {COLUMNS} FROM persistent_entry {where}")
            rows = await cur.fetchall()
    rows.sort(key=lambda r: (SECTION_ORDER[r["section"]], r["status"] == "retired", r["created"], r["id"]))
    return rows


async def add_persistent_entry(pool, *, section: str, text: str, source: str = "user", replaces_id: int | None = None,
                    now: datetime | None = None, memory: Memory | None = None) -> dict[str, Any]:
    """Add a fact. Text already live in the section counts as a sighting instead. A fact that replaces another, or
    comes from a note, starts provisional. Returns {"action", "entry", "also_changed", "evicted"}."""
    memory, now = memory or Memory(), now or datetime.now(timezone.utc)
    if section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}; the sections are {list(SECTIONS)}")
    checked = Entry(text=text, source=source, created=now)   # tidies the text, validates length and source
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {COLUMNS} FROM persistent_entry WHERE section = %s AND lower(text) = lower(%s) AND status <> 'retired' FOR UPDATE",
                (section, checked.text),
            )
            twin = await cur.fetchone()
            if twin:
                entry, also = await _confirm(cur, twin, now, memory)
                return {"action": "confirmed", "entry": entry, "also_changed": also, "evicted": []}
            if replaces_id is not None:
                old = await _get(cur, replaces_id)
                if old is None or old["status"] not in LIVE_STATUSES or old["section"] != section:
                    raise ValueError("The entry to replace must be a live entry in the same section.")
            status = "provisional" if replaces_id is not None or source == "note" else "active"
            await cur.execute(
                f"INSERT INTO persistent_entry (section, text, source, created, status, replaces_id, seen_days) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {COLUMNS}",
                (section, checked.text, source, now, status, replaces_id, [ist_day(now)]),
            )
            new = await cur.fetchone()
            await _log(cur, "add", str(new["id"]), [], [ser(new)])
            evicted = await _evict_to_cap(cur, memory, frozenset({new["id"]}))
    return {"action": "added", "entry": ser(new), "also_changed": [], "evicted": evicted}


async def edit_persistent_entry(pool, entry_id: int, *, text: str | None = None, section: str | None = None,
                     now: datetime | None = None, memory: Memory | None = None) -> dict[str, Any] | None:
    """Change the text or section of a live entry in place (it keeps its created date). None if there is no such entry."""
    memory, now = memory or Memory(), now or datetime.now(timezone.utc)
    if section is not None and section not in SECTIONS:
        raise ValueError(f"unknown section {section!r}; the sections are {list(SECTIONS)}")
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            e = await _get(cur, entry_id)
            if e is None:
                return None
            if e["status"] not in LIVE_STATUSES:
                raise ValueError("A retired entry can't be edited.")
            new_text = Entry(text=text, created=now).text if text is not None else e["text"]
            new_section = section or e["section"]
            if (new_text, new_section) == (e["text"], e["section"]):
                return {"entry": ser(e), "evicted": []}
            await cur.execute(
                "SELECT 1 FROM persistent_entry WHERE section = %s AND lower(text) = lower(%s) AND status <> 'retired' AND id <> %s",
                (new_section, new_text, entry_id),
            )
            if await cur.fetchone():
                raise ValueError("That text is already in the section.")
            new = await _set(cur, entry_id, text=new_text, section=new_section)
            await _log(cur, "edit", str(entry_id), [ser(e)], [ser(new)])
            evicted = await _evict_to_cap(cur, memory, frozenset({entry_id}))
    return {"entry": ser(new), "evicted": evicted}


async def confirm_persistent_entry(pool, entry_id: int, *, now: datetime | None = None, memory: Memory | None = None) -> dict[str, Any] | None:
    """The user (or a repeat) says this is still true."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            e = await _get(cur, entry_id)
            if e is None:
                return None
            entry, also = await _confirm(cur, e, now or datetime.now(timezone.utc), memory or Memory())
    return {"entry": entry, "also_changed": also}


async def retire_persistent_entry(pool, entry_id: int, *, reason: str = "user") -> dict[str, Any] | None:
    """Take an entry out of every prompt. It stays in the table and the log."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            e = await _get(cur, entry_id)
            if e is None:
                return None
            if e["status"] == "retired":
                raise ValueError("That entry is already retired.")
            new = await _set(cur, entry_id, status="retired", retired_reason=reason)
            await _log(cur, "retire", str(entry_id), [ser(e)], [ser(new)])
    return ser(new)


async def undo_persistent_change(pool, diff_id: int) -> dict[str, Any] | None:
    """Reverse one logged change, if the entries it touched are still as that change left them. The undo is logged too."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT id, source_type, before, after FROM diff_log WHERE id = %s FOR UPDATE", (diff_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            if row["source_type"] not in ACTIONS:
                raise ValueError("That change can't be undone.")
            await cur.execute("SELECT 1 FROM diff_log WHERE source_type = 'undo' AND source_id = %s", (str(diff_id),))
            if await cur.fetchone():
                raise ValueError("That change was already undone.")

            before, after = row["before"]["entries"], row["after"]["entries"]
            current = []
            for state in after:
                found = await _get(cur, state["id"])
                if found is None or ser(found) != state:
                    raise ValueError("That entry has changed since, so this can't be undone safely.")
                current.append(ser(found))
            try:
                async with conn.transaction():
                    for state in before:
                        await _set(cur, state["id"], **_restore_values(state))
                    kept = {s["id"] for s in before}
                    for state in after:
                        if state["id"] not in kept:
                            await cur.execute("DELETE FROM persistent_entry WHERE id = %s", (state["id"],))
            except UniqueViolation:
                raise ValueError("Another live entry now has that text, so this can't be undone.") from None
            await _log(cur, "undo", str(diff_id), current, before)
    return {"undone": diff_id, "restored": before}
