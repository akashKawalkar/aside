"""A reason on each calendar block (planning design, step 5).

Revision ID: add_schedule_why
Revises: add_practiced_day
Create Date: 2026-11-15

The planner gives every block a short "why". It is kept here and also sent to Google Calendar as the event's description.
Nullable text, so every existing row and every existing writer is unaffected. Additive only.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_schedule_why"
down_revision: Union[str, Sequence[str], None] = "add_practiced_day"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE schedule ADD COLUMN IF NOT EXISTS why text")


def downgrade() -> None:
    op.execute("ALTER TABLE schedule DROP COLUMN IF EXISTS why")
