"""persistent file as one row per entry; the old static/dynamic table and the old corrections table are dropped

Revision ID: add_persistent_entries
Revises: add_data_quality
Create Date: 2026-10-06

Destructive on purpose (user decision): `persistent_file` held only smoke-test data and `corrections` one smoke-test
row, and `mark_wrong_log` replaces corrections. Dump first: backups/aside_*_pre_persistent_entries.dump.
downgrade() recreates the two old tables empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_persistent_entries"
down_revision: Union[str, Sequence[str], None] = "add_data_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "persistent_entry",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("section", sa.String(length=20), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False, server_default="user"),
        sa.Column("created", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("last_confirmed", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="active"),
        sa.Column("retired_reason", sa.String(length=20), nullable=True),   # replaced | evicted | user
        sa.Column("replaces_id", sa.Integer(), sa.ForeignKey("persistent_entry.id", ondelete="SET NULL"), nullable=True),
        sa.Column("seen_days", postgresql.ARRAY(sa.Date()), nullable=False, server_default=sa.text("'{}'")),
        sa.CheckConstraint("section IN ('identity', 'preferences', 'routine', 'goals', 'people')", name="ck_pe_section"),
        sa.CheckConstraint("source IN ('user', 'agent', 'note', 'pattern')", name="ck_pe_source"),
        sa.CheckConstraint("status IN ('active', 'provisional', 'retired')", name="ck_pe_status"),
        sa.CheckConstraint("evidence_count >= 1", name="ck_pe_evidence"),
        sa.CheckConstraint("last_confirmed IS NULL OR last_confirmed >= created", name="ck_pe_confirmed_order"),
    )
    # Two live entries in one section may not share text (case-insensitive); a retired one may repeat.
    op.create_index("uq_pe_live_text", "persistent_entry", ["section", sa.text("lower(text)")], unique=True,
                    postgresql_where=sa.text("status <> 'retired'"))
    op.create_index("ix_pe_status", "persistent_entry", ["status"])

    op.drop_table("persistent_file")
    op.drop_table("corrections")


def downgrade() -> None:
    op.create_table(
        "corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("snapshot", JSONB, nullable=False),
        sa.Column("desired_fix", JSONB, nullable=False),
        sa.Column("target_ref", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
    )
    op.create_table(
        "persistent_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("static", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dynamic", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW),
    )
    op.drop_index("ix_pe_status", table_name="persistent_entry")
    op.drop_index("uq_pe_live_text", table_name="persistent_entry")
    op.drop_table("persistent_entry")
