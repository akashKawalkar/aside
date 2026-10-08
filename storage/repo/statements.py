from typing import List, Dict, Any
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

# Statements
async def list_statements(pool: AsyncConnectionPool) -> List[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM statements ORDER BY id")
            return await cur.fetchall()

async def delete_statement(pool: AsyncConnectionPool, statement_id: int) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM statements WHERE id = %s", (statement_id,))
            return cur.rowcount > 0

async def insert_statement(pool: AsyncConnectionPool, text: str, source: str) -> Dict[str, Any]:
    """Raw text the user said about their day (plan §3.5): source is `journal`, `note` or `instruction`. Never edited."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("INSERT INTO statements (text, source) VALUES (%s, %s) RETURNING *", (text, source))
            return await cur.fetchone()

# Candidate Items
async def list_candidate_items(pool: AsyncConnectionPool) -> List[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM candidate_items ORDER BY id")
            return await cur.fetchall()

async def delete_candidate_item(pool: AsyncConnectionPool, item_id: int) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM candidate_items WHERE id = %s", (item_id,))
            return cur.rowcount > 0

# Instruction Records
async def list_instruction_records(pool: AsyncConnectionPool) -> List[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM instruction_records ORDER BY id")
            return await cur.fetchall()

async def delete_instruction_record(pool: AsyncConnectionPool, record_id: int) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM instruction_records WHERE id = %s", (record_id,))
            return cur.rowcount > 0



async def insert_instruction_record(pool: AsyncConnectionPool, text: str, valid_from=None, valid_until=None) -> Dict[str, Any]:
    """A one-shot instruction scoped to a date range; kept afterwards for the pattern finder (plan 3.5)."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO instruction_records (text, valid_from, valid_until) VALUES (%s, %s, %s) RETURNING *",
                (text, valid_from, valid_until),
            )
            return await cur.fetchone()

async def insert_candidate_item(pool: AsyncConnectionPool, item_type: str, text: str, effect: str = None, valid_from=None, valid_until=None, confidence: float = 1.0, source_id: str = None, reason: str = None) -> Dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO candidate_items (item_type, text, effect, valid_from, valid_until, confidence, source_id, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (item_type, text, effect, valid_from, valid_until, confidence, source_id, reason),
            )
            return await cur.fetchone()

async def get_statements_for_day(pool: AsyncConnectionPool, target_date) -> List[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM statements WHERE date(created_at AT TIME ZONE 'Asia/Kolkata') = %s ORDER BY id", (target_date,))
            return await cur.fetchall()
