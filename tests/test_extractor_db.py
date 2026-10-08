import json
import pytest

from config import Privacy
from datetime import date
from llm.client import Completion
from llm.fake import FakeClient
from extractor.job import extract_for_day
from storage.database import make_pool
from storage.repo.statements import delete_statement

@pytest.fixture
async def pool():
    pool = make_pool()
    await pool.open()
    yield pool
    await pool.close()

@pytest.mark.asyncio
async def test_extract_for_day_empty(pool):
    client = FakeClient([Completion("{}", "fake")])
    items = await extract_for_day(client, pool, date(2001, 1, 1))
    assert items == []

@pytest.mark.asyncio
async def test_extract_for_day_with_items(pool):
    # Insert some dummy statements
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO statements (text, source, created_at) VALUES (%s, %s, %s) RETURNING id",
                ("I like cheese", "note", "2001-01-01 12:00:00+05:30")
            )
            stmt_id = (await cur.fetchone())[0]

    # mock response
    mock_response = json.dumps({
        "items": [
            {
                "type": "preference",
                "text": "Likes cheese",
                "effect": "User enjoys cheese",
                "valid_from": "2001-01-01",
                "valid_until": None,
                "confidence": 0.9,
                "source_id": str(stmt_id),
                "reason": "Explicitly stated"
            }
        ]
    })
    
    client = FakeClient([Completion(mock_response, "fake")])
    
    items = await extract_for_day(client, pool, date(2001, 1, 1))
    assert len(items) == 1
    assert items[0]["item_type"] == "preference"
    assert items[0]["text"] == "Likes cheese"
    
    # Cleanup
    await delete_statement(pool, stmt_id)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM candidate_items WHERE id = %s", (items[0]["id"],))



async def _statement(pool, text, source, ts):
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("INSERT INTO statements (text, source, created_at) VALUES (%s, %s, %s) RETURNING id", (text, source, ts))
            return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_extractor_never_sends_denied_statements(pool):
    sid = await _statement(pool, "zz private diary line", "journal", "2001-01-02 12:00:00+05:30")
    try:
        client = FakeClient([Completion('{"items": []}', "fake")])
        deny_journal = Privacy(deny_tags=["journal"])          # the rule is tested explicitly: the shipped default may change
        assert await extract_for_day(client, pool, date(2001, 1, 2), privacy=deny_journal) == []
        assert client.calls == []       # the journal is denied by the rule, so there was nothing to send
    finally:
        await delete_statement(pool, sid)


@pytest.mark.asyncio
async def test_extractor_skips_malformed_items_and_keeps_the_rest(pool):
    sid = await _statement(pool, "zz plan to run tomorrow", "note", "2001-01-03 12:00:00+05:30")
    reply = json.dumps({"items": [
        {"text": "no type"},
        {"type": "bogus", "text": "bad type"},
        {"type": "plan", "text": "Run tomorrow", "confidence": "high", "valid_from": "not a date", "source_id": None},
    ]})
    stored = []
    try:
        stored = await extract_for_day(FakeClient([Completion(reply, "fake")]), pool, date(2001, 1, 3))
        assert [(r["item_type"], r["text"], r["source_id"], r["valid_from"]) for r in stored] == [("plan", "Run tomorrow", None, date(2001, 1, 3))]   # an unreadable date falls back to the statement's day
    finally:
        await delete_statement(pool, sid)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                for r in stored:
                    await cur.execute("DELETE FROM candidate_items WHERE id = %s", (r["id"],))


@pytest.mark.asyncio
async def test_extractor_worker_does_nothing_while_background_is_off(pool):
    from extractor.worker import run_extractor_once
    assert await run_extractor_once(pool, False) == 0
