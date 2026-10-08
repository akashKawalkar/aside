"""data quality: schedule log/origin/snapshot, task due log, monitoring log, heartbeat, day record, mark-wrong log

Revision ID: add_data_quality
Revises: add_compile_log_llm_trace
Create Date: 2026-10-06

Additive, except one deliberate change: fk_sessions_first_event_id is dropped so sessions (derived data)
can outlive the raw events they were built from once retention pruning is applied. The unique constraint on
first_event_id stays, so the sessionizer is still idempotent. downgrade() puts the foreign key back.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_data_quality"
down_revision: Union[str, Sequence[str], None] = "add_compile_log_llm_trace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
NOW = sa.text("now()")


def upgrade() -> None:
    # schedule: who made an entry, and whether the user changed a generated one
    op.add_column("schedule", sa.Column("origin", sa.String(length=20), nullable=False, server_default="user"))
    op.add_column("schedule", sa.Column("edited_by_user", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_check_constraint("ck_schedule_origin", "schedule", "origin IN ('user', 'generated')")

    op.create_table(
        "schedule_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("schedule_id", sa.Integer(), nullable=False),   # no FK: the entry may be deleted
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("before", JSONB, nullable=True),
        sa.Column("after", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("action IN ('create', 'edit', 'delete')", name="ck_schedule_log_action"),
    )
    op.create_index("ix_schedule_log_schedule_id", "schedule_log", ["schedule_id"])
    op.create_index("ix_schedule_log_created_at", "schedule_log", ["created_at"])

    op.create_table(
        "schedule_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False, unique=True),
        sa.Column("entries", JSONB, nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )

    op.create_table(
        "task_due_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("old_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_task_due_log_task_id", "task_due_log", ["task_id"])

    op.create_table(
        "monitoring_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_monitoring_log_created_at", "monitoring_log", ["created_at"])

    op.create_table(
        "heartbeat_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("collector_ok", sa.Boolean(), nullable=False),
        sa.Column("ingestor_ok", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_heartbeat_log_ts", "heartbeat_log", ["ts"])

    op.create_table(
        "day_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False, unique=True),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )

    op.create_table(
        "mark_wrong_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("compile_log_id", sa.Integer(), sa.ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )

    # Sessions are derived data: they stay when old raw events are pruned.
    op.drop_constraint("fk_sessions_first_event_id", "sessions", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key("fk_sessions_first_event_id", "sessions", "events", ["first_event_id"], ["id"])
    for table in ("mark_wrong_log", "day_record", "heartbeat_log", "monitoring_log", "task_due_log",
                  "schedule_snapshot", "schedule_log"):
        op.drop_table(table)
    op.drop_constraint("ck_schedule_origin", "schedule", type_="check")
    op.drop_column("schedule", "edited_by_user")
    op.drop_column("schedule", "origin")
