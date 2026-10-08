"""Cloud edition groundwork (step 5.1.3 of the cloud plan).

Revision ID: add_cloud_support
Revises: add_observations
Create Date: 2026-10-08

Additive only:
- job_run: one row per step of the nightly job (reason on failure).
- review_state: review settings + latest review as key/value JSON (was files in data/).
- tasks: dropped_at / dropped_reason / slip_count (stale tasks are dropped, not lingering); gtask_id / gtask_updated (Google Tasks link).
- schedule: calendar link (gcal_event_id, gcal_updated), soft delete (deleted_at), task_id link.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "add_cloud_support"
down_revision: Union[str, Sequence[str], None] = "add_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),      # nightly | morning_retry | manual
        sa.Column("step", sa.String(length=40), nullable=False),      # e.g. sessionize, extract, patterns, generate
        sa.Column("day", sa.Date(), nullable=True),                   # the IST day the run is for
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("detail", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_job_run_started_at", "job_run", ["started_at"])
    op.create_index("ix_job_run_day_step", "job_run", ["day", "step"])

    op.create_table(
        "review_state",
        sa.Column("key", sa.String(length=40), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )

    op.add_column("tasks", sa.Column("dropped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("dropped_reason", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("slip_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tasks", sa.Column("gtask_id", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("gtask_updated", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ux_tasks_gtask_id", "tasks", ["gtask_id"], unique=True, postgresql_where=sa.text("gtask_id IS NOT NULL"))

    op.add_column("schedule", sa.Column("gcal_event_id", sa.Text(), nullable=True))
    op.add_column("schedule", sa.Column("gcal_updated", sa.DateTime(timezone=True), nullable=True))
    op.add_column("schedule", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("schedule", sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ux_schedule_gcal_event_id", "schedule", ["gcal_event_id"], unique=True,
                    postgresql_where=sa.text("gcal_event_id IS NOT NULL"))
    op.create_index("ix_schedule_task_id", "schedule", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_schedule_task_id", table_name="schedule")
    op.drop_index("ux_schedule_gcal_event_id", table_name="schedule")
    for col in ("task_id", "deleted_at", "gcal_updated", "gcal_event_id"):
        op.drop_column("schedule", col)
    op.drop_index("ux_tasks_gtask_id", table_name="tasks")
    for col in ("gtask_updated", "gtask_id", "slip_count", "dropped_reason", "dropped_at"):
        op.drop_column("tasks", col)
    op.drop_table("review_state")
    op.drop_index("ix_job_run_day_step", table_name="job_run")
    op.drop_index("ix_job_run_started_at", table_name="job_run")
    op.drop_table("job_run")
