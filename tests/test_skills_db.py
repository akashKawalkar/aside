"""Skills: every repo function against the real table (so model/DB drift cannot recur: the M3c repo selected
columns the migration never created), plus the pure trigger matching. Test rows are named `zz...`; only they are
removed afterwards. DB tests skip if the database is unreachable."""
from __future__ import annotations

import pytest

import storage
from knowledge.skills import load_relevant_skills, match_score, triggers
from storage.repo import skills as repo


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
            await cur.execute("DELETE FROM skills WHERE name LIKE 'zz%'")
    await p.close()


def test_triggers_split_and_tidy():
    assert triggers(" email, writing ,, ") == ["email", "writing"]
    assert triggers("") == [] and triggers(None) == []


@pytest.mark.parametrize("use_when,text,score", [
    ("gym", "i went to the gym today", 1),
    ("gym", "the gymkhana was fun", 0),          # whole words only
    ("email, write", "write an email to Rahul", 2),
    ("Email", "EMAIL him", 1),                    # case-insensitive
    ("c++", "learning c++ now", 1),               # regex characters are escaped
    ("email", None, 0),                           # a missing query must not raise
    ("email", "", 0),
    ("", "anything", 0),
])
def test_match_score(use_when, text, score):
    assert match_score(use_when, text) == score


async def test_crud_round_trip_uses_the_real_columns(pool):
    made = await repo.create_skill(pool, "zz-format", "Use bullets.", use_when="summary, summarise", affects="reply format")
    assert {"id", "name", "use_when", "affects", "body", "usage_count", "correction_count", "created_at"} <= made.keys()
    assert (made["use_when"], made["affects"], made["usage_count"]) == ("summary, summarise", "reply format", 0)

    assert (await repo.get_skill(pool, made["id"]))["name"] == "zz-format"
    assert made["id"] in [s["id"] for s in await repo.list_skills(pool)]

    edited = await repo.update_skill(pool, made["id"], body="Use numbered steps.", usage_count=2)
    assert (edited["body"], edited["usage_count"], edited["name"]) == ("Use numbered steps.", 2, "zz-format")

    # Unknown columns are ignored, not interpolated into SQL.
    same = await repo.update_skill(pool, made["id"], **{"body = 'x', name": "boom", "tags": ["a"]})
    assert same["body"] == "Use numbered steps."

    assert await repo.delete_skill(pool, made["id"]) is True
    assert await repo.get_skill(pool, made["id"]) is None
    assert await repo.delete_skill(pool, made["id"]) is False


async def test_loader_picks_by_trigger_ranks_by_matches_and_caps(pool):
    one = await repo.create_skill(pool, "zz-one", "A", use_when="zzalpha")
    two = await repo.create_skill(pool, "zz-two", "B", use_when="zzalpha, zzbeta")
    await repo.create_skill(pool, "zz-none", "C", use_when="zzgamma")

    got = await load_relevant_skills(pool, "need zzalpha and zzbeta help", limit=2)
    assert [s["id"] for s in got] == [two["id"], one["id"]]          # two matches outrank one
    assert [s["id"] for s in await load_relevant_skills(pool, "zzalpha", limit=1)] in ([one["id"]], [two["id"]])
    assert [s for s in await load_relevant_skills(pool, "zzalphabet") if s["name"].startswith("zz")] == []   # no substring hit
    assert await load_relevant_skills(pool, None) == []
