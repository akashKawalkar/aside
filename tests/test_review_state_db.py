"""Review settings/latest in the review_state table (shared by the laptop and the nightly job)."""
from __future__ import annotations

import pytest

import storage
from review.store import DbBackend, ReviewSettings, ReviewStore


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
            await cur.execute("DELETE FROM review_state WHERE key IN ('settings', 'latest', 'zz_key')")
    await p.close()


async def test_set_get_overwrite(pool):
    assert await storage.get_review_state(pool, "zz_key") is None
    await storage.set_review_state(pool, "zz_key", {"a": 1})
    await storage.set_review_state(pool, "zz_key", {"a": 2})
    assert await storage.get_review_state(pool, "zz_key") == {"a": 2}


async def test_store_on_db_backend_and_legacy_import(pool, tmp_path):
    (tmp_path / "review_settings.json").write_text('{"time": "22:00"}')
    store = ReviewStore(DbBackend(lambda: pool))

    assert await store.settings() == ReviewSettings()
    assert await store.import_legacy_files(tmp_path) == ["settings"]
    assert (await store.settings()).time == "22:00"
    assert await store.import_legacy_files(tmp_path) == []   # already present: not overwritten
    assert await store.latest() is None
