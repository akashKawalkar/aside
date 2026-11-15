"""Database side of the digital schedule: the rules table and reading one day of events."""
from __future__ import annotations

from datetime import date

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from digital.builder import Ev, day_window
from digital.rules import Rule
from digital.seed import SEED_RULES


def _row_to_rule(r: dict) -> Rule:
    return Rule(
        category=r["category"], label=r["label"], app=r["app"], domain=r["domain"], title_has=tuple(r["title_has"] or ()),
        source=r["source"], priority=r["priority"], use_title=r["use_title"], note=r["note"], id=r["id"], enabled=r["enabled"],
    )


async def seed_if_empty(pool: AsyncConnectionPool) -> int:
    """Copies the seed into the table the first time. Returns how many rows were added."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) FROM category_rule")
            if (await cur.fetchone())[0]:
                return 0
            for r in SEED_RULES:
                await cur.execute(
                    "INSERT INTO category_rule (priority, source, app, domain, title_has, category, label, use_title, origin, note) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'seed', %s)",
                    (r.priority, r.source, r.app, r.domain, list(r.title_has), r.category, r.label, r.use_title, r.note),
                )
            return len(SEED_RULES)


async def load_rules(pool: AsyncConnectionPool) -> list[Rule]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM category_rule WHERE enabled ORDER BY priority DESC, id")
            return [_row_to_rule(r) for r in await cur.fetchall()]


async def load_events(pool: AsyncConnectionPool, day: date) -> list[Ev]:
    """Every laptop, browser and phone event that touches the 05:00-to-05:00 day. Read only."""
    lo, hi = day_window(day)
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT source, kind, app, domain, window_title, ts_start, ts_end FROM events "
                "WHERE ts_end > %s AND ts_start < %s AND ts_end IS NOT NULL "
                "AND ((source = 'laptop' AND kind IN ('app_focus', 'idle')) OR source = 'browser' "
                "     OR (source = 'android' AND kind = 'phone_app_focus')) ORDER BY ts_start",
                (lo, hi),
            )
            return [
                Ev(r["source"], r["kind"], r["ts_start"], r["ts_end"], r["app"], r["domain"], r["window_title"])
                for r in await cur.fetchall()
            ]
