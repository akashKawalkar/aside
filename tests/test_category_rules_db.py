"""The category rules table (migration add_category_rules), against the test database."""
from __future__ import annotations

import pytest

import storage
from digital import store
from digital.rules import classify, ordered
from digital.seed import SEED_RULES


@pytest.fixture
async def pool():
    p = storage.make_pool()
    try:
        await p.open(wait=True, timeout=5)
    except Exception:
        pytest.skip("database not reachable")
    async with p.connection() as conn:
        await conn.execute("DELETE FROM category_rule")           # the test database only; rules there are scratch
    yield p
    async with p.connection() as conn:
        await conn.execute("DELETE FROM category_rule")
    await p.close()


async def test_seed_fills_an_empty_table_once(pool):
    assert await store.seed_if_empty(pool) == len(SEED_RULES)
    assert await store.seed_if_empty(pool) == 0


async def test_loaded_rules_classify_like_the_seed(pool):
    await store.seed_if_empty(pool)
    rules = await store.load_rules(pool)
    assert len(rules) == len(SEED_RULES)
    m = classify(ordered(rules), "laptop", "chrome.exe", "www.youtube.com", "PyTorch tutorial - YouTube")
    assert m.category == "study"


async def test_a_disabled_rule_is_not_loaded(pool):
    await store.seed_if_empty(pool)
    async with pool.connection() as conn:
        await conn.execute("UPDATE category_rule SET enabled = false WHERE domain = 'instagram.com'")
    rules = await store.load_rules(pool)
    assert all(r.domain != "instagram.com" for r in rules)
