"""Practiced schedule per day, and the cached expected categories of calendar titles (planning design, step 2).

Revision ID: add_practiced_day
Revises: add_category_rules
Create Date: 2026-11-15

`practiced_day` holds the code-built practiced schedule of one 05:00-to-05:00 day (`data`) and, later, the digest of it
(`digest`). It is separate from `day_record` on purpose: the nightly review writes that row with ON CONFLICT DO NOTHING, so
a second writer on the same row could make either job skip its half.

`title_tag` caches what each calendar title is expected to look like on the devices (one cheap model call per new title,
reviewable and editable). Additive only.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_practiced_day"
down_revision: Union[str, Sequence[str], None] = "add_category_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = """
CREATE TABLE IF NOT EXISTS practiced_day (
    day        date PRIMARY KEY,
    data       jsonb NOT NULL,
    digest     jsonb,
    built_at   timestamptz NOT NULL DEFAULT now(),
    digest_at  timestamptz
);

CREATE TABLE IF NOT EXISTS title_tag (
    title      text PRIMARY KEY,
    expected   text[] NOT NULL DEFAULT '{}',
    observable boolean NOT NULL DEFAULT true,
    origin     text NOT NULL DEFAULT 'llm' CHECK (origin IN ('llm', 'user')),
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(TABLES)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS title_tag")
    op.execute("DROP TABLE IF EXISTS practiced_day")
