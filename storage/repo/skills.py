# storage/repo/skills.py — the skills table: descriptor (name, use_when, affects) + body + usage/correction counts.
from typing import Any, Dict, List, Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

COLUMNS = "id, name, use_when, affects, body, usage_count, correction_count, created_at"
EDITABLE = ("name", "use_when", "affects", "body", "usage_count", "correction_count")   # whitelist for update_skill


async def create_skill(pool: AsyncConnectionPool, name: str, body: str, use_when: str = "", affects: str = "") -> Dict[str, Any]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"INSERT INTO skills (name, body, use_when, affects) VALUES (%s, %s, %s, %s) RETURNING {COLUMNS}",
                (name, body, use_when, affects),
            )
            return await cur.fetchone()


async def get_skill(pool: AsyncConnectionPool, skill_id: int) -> Optional[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM skills WHERE id = %s", (skill_id,))
            return await cur.fetchone()


async def list_skills(pool: AsyncConnectionPool) -> List[Dict[str, Any]]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM skills ORDER BY id")
            return await cur.fetchall()


async def update_skill(pool: AsyncConnectionPool, skill_id: int, **kwargs) -> Optional[Dict[str, Any]]:
    fields = {k: v for k, v in kwargs.items() if k in EDITABLE}
    if not fields:
        return await get_skill(pool, skill_id)

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"UPDATE skills SET {', '.join(f'{k} = %s' for k in fields)} WHERE id = %s RETURNING {COLUMNS}",
                [*fields.values(), skill_id],
            )
            return await cur.fetchone()


async def delete_skill(pool: AsyncConnectionPool, skill_id: int) -> bool:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM skills WHERE id = %s", (skill_id,))
            return cur.rowcount > 0
