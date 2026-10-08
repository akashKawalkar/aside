"""schedule_draft: one row per generated-schedule version (M6a)

Revision ID: add_schedule_draft
Revises: 061552b2195b
Create Date: 2026-10-07

Additive only (a new table). Dump taken first: backups/aside_*_pre_schedule_draft.dump.
`proposed` is the immutable record of what was generated; `entries` is the working copy the user edits and accepts.
One day can have many versions (a regeneration supersedes the previous one, which is kept) but only one OPEN
version at a time, enforced by a partial unique index. Version 1's `proposed` is the "first generated draft" that plan section 3.6 compares against the day's final schedule.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "add_schedule_draft"
down_revision: Union[str, Sequence[str], None] = "061552b2195b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "schedule_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_day", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("schedule_draft.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="open"),
        sa.Column("source", sa.String(length=12), nullable=False),            # llm | placeholder
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("proposed", JSONB, nullable=False),                         # the entries exactly as generated; never touched afterwards
        sa.Column("entries", JSONB, nullable=False),                          # the working copy: [{title,start_at,end_at,reason,state,locked,edited,schedule_id}]
        sa.Column("rejected", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),   # what the validator dropped, and why
        sa.Column("inputs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),     # what the gather step offered
        sa.Column("compile_log_id", sa.Integer(), sa.ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'superseded', 'accepted', 'discarded')", name="ck_schedule_draft_status"),
        sa.CheckConstraint("source IN ('llm', 'placeholder')", name="ck_schedule_draft_source"),
        sa.CheckConstraint("version >= 1", name="ck_schedule_draft_version"),
        sa.UniqueConstraint("target_day", "version", name="uq_schedule_draft_day_version"),
    )
    op.create_index("ix_schedule_draft_target_day", "schedule_draft", ["target_day"])
    op.create_index(
        "uq_schedule_draft_one_open", "schedule_draft", ["target_day"], unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("uq_schedule_draft_one_open", table_name="schedule_draft")
    op.drop_index("ix_schedule_draft_target_day", table_name="schedule_draft")
    op.drop_table("schedule_draft")
