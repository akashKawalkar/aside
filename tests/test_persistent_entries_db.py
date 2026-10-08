"""Integration tests for the persistent file against the real database. Every entry text starts with `zz`; only those
rows (and the diff_log rows about them) are removed afterwards. Skipped if the database is unreachable."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import storage
from config import Memory
from context.persistent_schema import Entry

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
BIG = Memory(cap_tokens=10**9)


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    yield p
    async with p.connection() as conn:
        async with conn.cursor() as cur:
            # Undoing an add deletes the entry, so find the log rows by their content, not by id.
            await cur.execute("DELETE FROM diff_log WHERE before::text LIKE '%\"text\": \"zz%' OR after::text LIKE '%\"text\": \"zz%'")
            await cur.execute("DELETE FROM persistent_entry WHERE text LIKE 'zz%'")
    await p.close()


async def diff_rows(pool, entry_id):
    return [d for d in await storage.read_all_diffs(pool) if d["source_id"] == str(entry_id) and d["source_type"] != "undo"]


async def add(pool, text, section="preferences", **kw):
    kw.setdefault("memory", BIG)
    kw.setdefault("now", T0)
    return await storage.add_persistent_entry(pool, section=section, text=text, **kw)


async def test_add_stores_a_schema_valid_entry_and_logs_it(pool):
    out = await add(pool, "zz  Replies   in under 80 words")
    e = out["entry"]
    assert out["action"] == "added" and e["text"] == "zz Replies in under 80 words"   # whitespace tidied
    assert (e["source"], e["status"], e["evidence_count"], e["seen_days"]) == ("user", "active", 1, ["2026-10-01"])
    rows = [r for r in await storage.list_persistent_entries(pool) if r["id"] == e["id"]]
    Entry(**{k: rows[0][k] for k in ("text", "source", "created", "last_confirmed", "evidence_count", "status")})   # fits the schema
    log = await diff_rows(pool, e["id"])
    assert [d["source_type"] for d in log] == ["add"] and log[0]["added"] and not log[0]["removed"]


async def test_unknown_section_and_blank_text_are_refused(pool):
    with pytest.raises(ValueError):
        await add(pool, "zz x", section="secrets")
    with pytest.raises(ValueError):
        await add(pool, "   ")


async def test_restating_a_live_fact_counts_as_a_sighting_not_a_duplicate(pool):
    first = await add(pool, "zz Plays tennis on Saturdays", "routine")
    again = await add(pool, "ZZ plays TENNIS on saturdays", "routine", now=T0 + timedelta(hours=2))
    assert again["action"] == "confirmed" and again["entry"]["id"] == first["entry"]["id"]
    assert again["entry"]["evidence_count"] == 2 and again["entry"]["seen_days"] == ["2026-10-01"]   # same day: one day
    assert len([r for r in await storage.list_persistent_entries(pool) if r["text"].lower().startswith("zz plays tennis")]) == 1


async def test_replacement_is_provisional_then_settles_after_three_separate_days_and_retires_the_old(pool):
    old = (await add(pool, "zz Lives in Pune", "identity"))["entry"]
    new = (await add(pool, "zz Lives in Mumbai", "identity", replaces_id=old["id"]))["entry"]
    assert new["status"] == "provisional" and new["replaces_id"] == old["id"]

    day2 = await storage.confirm_persistent_entry(pool, new["id"], now=T0 + timedelta(days=1), memory=BIG)
    assert day2["entry"]["status"] == "provisional"
    same_day = await storage.confirm_persistent_entry(pool, new["id"], now=T0 + timedelta(days=1, hours=1), memory=BIG)
    assert same_day["entry"]["status"] == "provisional"                      # the same day twice is still two days
    day3 = await storage.confirm_persistent_entry(pool, new["id"], now=T0 + timedelta(days=2), memory=BIG)
    assert day3["entry"]["status"] == "active"
    assert [(e["id"], e["status"], e["retired_reason"]) for e in day3["also_changed"]] == [(old["id"], "retired", "replaced")]

    live = {r["text"] for r in await storage.list_persistent_entries(pool, include_retired=False)}
    assert "zz Lives in Mumbai" in live and "zz Lives in Pune" not in live


async def test_replacing_needs_a_live_entry_in_the_same_section(pool):
    other = (await add(pool, "zz A goal", "goals"))["entry"]
    with pytest.raises(ValueError):
        await add(pool, "zz A preference", "preferences", replaces_id=other["id"])
    with pytest.raises(ValueError):
        await add(pool, "zz A preference", "preferences", replaces_id=999_999_999)


async def test_a_note_is_provisional_until_repeated(pool):
    e = (await add(pool, "zz Prefers short answers", source="note"))["entry"]
    assert e["status"] == "provisional" and e["replaces_id"] is None
    for d in (1, 2):
        out = await storage.confirm_persistent_entry(pool, e["id"], now=T0 + timedelta(days=d), memory=BIG)
    assert out["entry"]["status"] == "active" and out["also_changed"] == []


async def test_edit_keeps_created_and_is_logged_with_before_and_after(pool):
    e = (await add(pool, "zz Wakes at 6"))["entry"]
    out = await storage.edit_persistent_entry(pool, e["id"], text="zz Wakes at 7", section="routine", now=T0 + timedelta(days=1), memory=BIG)
    assert (out["entry"]["text"], out["entry"]["section"], out["entry"]["created"]) == ("zz Wakes at 7", "routine", e["created"])
    log = await diff_rows(pool, e["id"])
    assert [d["source_type"] for d in log] == ["add", "edit"]
    assert "Wakes at 6" in log[1]["removed"][0] and "Wakes at 7" in log[1]["added"][0]
    assert await storage.edit_persistent_entry(pool, 999_999_999, text="zz nope") is None


async def test_editing_into_an_existing_text_is_refused(pool):
    await add(pool, "zz Likes tea")
    other = (await add(pool, "zz Likes coffee"))["entry"]
    with pytest.raises(ValueError):
        await storage.edit_persistent_entry(pool, other["id"], text="ZZ likes tea", memory=BIG)


async def test_retire_hides_the_entry_but_keeps_it(pool):
    e = (await add(pool, "zz Old habit"))["entry"]
    out = await storage.retire_persistent_entry(pool, e["id"])
    assert (out["status"], out["retired_reason"]) == ("retired", "user")
    assert "zz Old habit" not in {r["text"] for r in await storage.list_persistent_entries(pool, include_retired=False)}
    assert "zz Old habit" in {r["text"] for r in await storage.list_persistent_entries(pool)}
    with pytest.raises(ValueError):
        await storage.retire_persistent_entry(pool, e["id"])
    with pytest.raises(ValueError):
        await storage.edit_persistent_entry(pool, e["id"], text="zz Revived")
    assert (await add(pool, "zz Old habit"))["action"] == "added"            # a retired entry may be written again


async def test_undo_reverses_add_edit_and_retire_and_is_itself_logged(pool):
    e = (await add(pool, "zz Undo me"))["entry"]
    await storage.edit_persistent_entry(pool, e["id"], text="zz Undo me edited", memory=BIG)
    await storage.retire_persistent_entry(pool, e["id"])
    log = await diff_rows(pool, e["id"])
    add_id, edit_id, retire_id = (d["id"] for d in log)

    with pytest.raises(ValueError):
        await storage.undo_persistent_change(pool, add_id)                   # the edit came after: not safe
    await storage.undo_persistent_change(pool, retire_id)
    [row] = [r for r in await storage.list_persistent_entries(pool) if r["id"] == e["id"]]
    assert row["status"] == "active" and row["retired_reason"] is None
    with pytest.raises(ValueError):
        await storage.undo_persistent_change(pool, retire_id)                # already undone
    await storage.undo_persistent_change(pool, edit_id)
    await storage.undo_persistent_change(pool, add_id)
    assert e["id"] not in {r["id"] for r in await storage.list_persistent_entries(pool)}

    diffs = {d["id"]: d for d in await storage.read_all_diffs(pool)}
    assert all(diffs[i]["undone"] for i in (add_id, edit_id, retire_id))
    assert await storage.undo_persistent_change(pool, 999_999_999) is None


async def test_undoing_a_settle_restores_both_entries(pool):
    old = (await add(pool, "zz Works days", "routine"))["entry"]
    new = (await add(pool, "zz Works nights", "routine", replaces_id=old["id"]))["entry"]
    for d in (1, 2):
        out = await storage.confirm_persistent_entry(pool, new["id"], now=T0 + timedelta(days=d), memory=BIG)
    settle = [d for d in await diff_rows(pool, new["id"]) if d["source_type"] == "confirm"][-1]
    await storage.undo_persistent_change(pool, settle["id"])
    by_id = {r["id"]: r for r in await storage.list_persistent_entries(pool)}
    assert (by_id[new["id"]]["status"], by_id[old["id"]]["status"]) == ("provisional", "active")
    assert len(by_id[new["id"]]["seen_days"]) == 2


async def test_cap_evicts_dynamic_sections_first_and_logs_each_eviction(pool):
    live = [r for r in await storage.list_persistent_entries(pool, include_retired=False) if not r["text"].startswith("zz")]
    if live:
        pytest.skip("real persistent-file entries exist; an eviction test could retire them")
    small = Memory(cap_tokens=10)
    goal = (await add(pool, "zz " + "g" * 20, "goals", memory=small))["entry"]
    keep = (await add(pool, "zz " + "k" * 20, "identity", memory=small))["entry"]    # pushes the total over the cap
    rows = {r["id"]: r for r in await storage.list_persistent_entries(pool)}
    assert rows[goal["id"]]["status"] == "retired" and rows[goal["id"]]["retired_reason"] == "evicted"
    assert rows[keep["id"]]["status"] == "active"                                      # the entry just written stays
    assert [d["source_type"] for d in await diff_rows(pool, goal["id"])] == ["add", "evict"]
    await storage.undo_persistent_change(pool, (await diff_rows(pool, goal["id"]))[1]["id"])
    assert {r["id"]: r for r in await storage.list_persistent_entries(pool)}[goal["id"]]["status"] == "active"
