"""Category rules table for the digital schedule (planning design, step 1).

Revision ID: add_category_rules
Revises: add_phone_planner_inputs
Create Date: 2026-11-15

One row per rule: which category and label an app, site or window title gets. Seeded from digital/seed.py the first time
the builder runs, then edited in place (origin 'user'); suggestions from the model arrive with origin 'llm' and are
auto-accepted, to be reviewed weekly. Additive only.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_category_rules"
down_revision: Union[str, Sequence[str], None] = "add_phone_planner_inputs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = """
CREATE TABLE IF NOT EXISTS category_rule (
    id         serial PRIMARY KEY,
    priority   integer NOT NULL DEFAULT 50,
    source     text CHECK (source IN ('laptop', 'phone')),
    app        text,
    domain     text,
    title_has  text[] NOT NULL DEFAULT '{}',
    category   text NOT NULL CHECK (category IN ('coding', 'study', 'ai_assist', 'work_admin', 'comms', 'social', 'media',
                   'job_search', 'networking', 'wellbeing', 'shopping', 'other', 'system', 'idle')),
    label      text NOT NULL,
    use_title  boolean NOT NULL DEFAULT true,
    origin     text NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'user', 'llm')),
    reviewed   boolean NOT NULL DEFAULT false,
    enabled    boolean NOT NULL DEFAULT true,
    note       text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


def upgrade() -> None:
    op.execute(TABLE)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS category_rule")
