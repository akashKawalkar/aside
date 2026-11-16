"""Persistence for category rules: which category and label an app, website or window title gets."""
from __future__ import annotations

from typing import Any
from psycopg.rows import dict_row

COLUMNS = "id, priority, source, app, domain, title_has, category, label, use_title, origin, reviewed, enabled, note, created_at"


async def list_category_rules(pool, *, review_only: bool = False) -> list[dict[str, Any]]:
    """List category rules. If review_only is true, only returns LLM-suggested rules that haven't been reviewed."""
    where = "WHERE origin = 'llm' AND NOT reviewed" if review_only else ""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {COLUMNS} FROM category_rule {where} ORDER BY priority DESC, id")
            return await cur.fetchall()


async def approve_category_rule(pool, rule_id: int) -> bool:
    """Mark an LLM-suggested category rule as reviewed."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("UPDATE category_rule SET reviewed = true WHERE id = %s RETURNING id", (rule_id,))
            return await cur.fetchone() is not None


async def update_category_rule(
    pool,
    rule_id: int,
    *,
    category: str | None = None,
    label: str | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
    note: str | None = None,
) -> bool:
    """Update fields on a rule and mark it reviewed (origin becomes user)."""
    sets, params = ["reviewed = true", "origin = 'user'"], []
    if category is not None:
        sets.append("category = %s")
        params.append(category)
    if label is not None:
        sets.append("label = %s")
        params.append(label)
    if enabled is not None:
        sets.append("enabled = %s")
        params.append(enabled)
    if priority is not None:
        sets.append("priority = %s")
        params.append(priority)
    if note is not None:
        sets.append("note = %s")
        params.append(note)
    params.append(rule_id)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"UPDATE category_rule SET {', '.join(sets)} WHERE id = %s RETURNING id", params)
            return await cur.fetchone() is not None


async def delete_category_rule(pool, rule_id: int) -> bool:
    """Delete a rule."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM category_rule WHERE id = %s RETURNING id", (rule_id,))
            return await cur.fetchone() is not None


async def create_category_rule(
    pool,
    *,
    category: str,
    label: str,
    app: str | None = None,
    domain: str | None = None,
    title_has: list[str] | None = None,
    source: str | None = None,
    priority: int = 50,
    use_title: bool = True,
    note: str = "",
) -> int:
    """Create a new user rule."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO category_rule
                   (priority, source, app, domain, title_has, category, label, use_title, origin, reviewed, enabled, note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'user', true, true, %s)
                   RETURNING id""",
                (priority, source, app, domain, title_has or [], category, label, use_title, note),
            )
            row = await cur.fetchone()
            return row[0]


async def save_suggested_rules(pool, title_tags: list[dict[str, Any]]) -> int:
    """Given title_tags from a digest, insert LLM-suggested rules that need Sunday review.

    Each title_tag has {"label": "<label>", "category": "<category>"}.
    For labels like "laptop: foo.exe" or "phone: com.bar" or "domain.com", parses out source/app/domain.
    """
    if not title_tags:
        return 0
    added = 0
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for item in title_tags:
                raw_label = item.get("label", "").strip()
                cat = item.get("category", "").strip().lower()
                if not raw_label or not cat:
                    continue
                # Parse source: app/domain
                source = None
                app = None
                domain = None
                clean_label = raw_label
                if raw_label.startswith("laptop:"):
                    source = "laptop"
                    target = raw_label[7:].strip()
                    if "." in target and not target.endswith(".exe"):
                        domain = target
                    else:
                        app = target
                    clean_label = app or domain or raw_label
                elif raw_label.startswith("phone:"):
                    source = "phone"
                    app = raw_label[6:].strip()
                    clean_label = app
                else:
                    if "." in raw_label:
                        domain = raw_label

                # Check if a rule for this app/domain already exists
                await cur.execute(
                    "SELECT id FROM category_rule WHERE (app = %s AND app IS NOT NULL) OR (domain = %s AND domain IS NOT NULL)",
                    (app, domain),
                )
                if await cur.fetchone():
                    continue

                await cur.execute(
                    """INSERT INTO category_rule
                       (priority, source, app, domain, title_has, category, label, use_title, origin, reviewed, enabled, note)
                       VALUES (40, %s, %s, %s, '{}', %s, %s, true, 'llm', false, true, 'suggested by digest')
                       ON CONFLICT DO NOTHING
                       RETURNING id""",
                    (source, app, domain, cat, clean_label),
                )
                if await cur.fetchone():
                    added += 1
    return added
