"""M7 pattern observations.

Revision ID: add_observations
Revises: add_schedule_draft
Create Date: 2026-10-07

Additive only: stores code-derived observations and their small lifecycle state.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "add_observations"
down_revision: Union[str, Sequence[str], None] = "add_schedule_draft"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("observation_key", sa.String(length=240), nullable=False, unique=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="candidate"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("misses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.Date(), nullable=True),
        sa.Column("last_seen", sa.Date(), nullable=True),
        sa.Column("last_evaluated_day", sa.Date(), nullable=True),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('candidate', 'active', 'questioned', 'dropped')", name="ck_observations_status"),
        sa.CheckConstraint("occurrences >= 0 AND misses >= 0", name="ck_observations_counts"),
    )
    op.create_index("ix_observations_status", "observations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_observations_status", table_name="observations")
    op.drop_table("observations")
