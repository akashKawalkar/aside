"""add session fields and event membership

Revision ID: add_session_fields
Revises: 025481487fef
Create Date: 2026-10-06

Sessions get a real kind, app, start, end, active time, event count and an
identity key (first_event_id). Membership is events.session_id: each event
belongs to at most one session, and events that never became part of one
(too short, or aged out while pending) stay NULL.

The columns are nullable at the column level and required by a NOT VALID
check constraint instead, so any pre-existing placeholder row is tolerated
while every new or updated row must be complete. Once no legacy rows
remain, run:  ALTER TABLE sessions VALIDATE CONSTRAINT ck_sessions_complete;
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "add_session_fields"
down_revision: Union[str, Sequence[str], None] = "025481487fef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("kind", sa.String(length=20), nullable=True))
    op.add_column("sessions", sa.Column("app", sa.Text(), nullable=True))
    op.add_column("sessions", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sessions", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    # Sum of fragment durations. Wall-clock time is ended_at - started_at.
    op.add_column("sessions", sa.Column("active_seconds", sa.Float(), nullable=True))
    op.add_column("sessions", sa.Column("event_count", sa.Integer(), nullable=True))
    op.add_column("sessions", sa.Column("first_event_id", sa.BigInteger(), nullable=True))

    op.create_foreign_key(
        "fk_sessions_first_event_id", "sessions", "events", ["first_event_id"], ["id"]
    )
    # One session per first event: makes re-running the sessionizer idempotent.
    op.create_unique_constraint("uq_sessions_first_event_id", "sessions", ["first_event_id"])
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"])

    op.create_check_constraint("ck_sessions_kind", "sessions", "kind IN ('app', 'idle')")
    op.create_check_constraint("ck_sessions_time_order", "sessions", "ended_at >= started_at")
    op.create_check_constraint(
        "ck_sessions_counts", "sessions", "active_seconds >= 0 AND event_count >= 1"
    )
    op.execute(
        """
        ALTER TABLE sessions ADD CONSTRAINT ck_sessions_complete CHECK (
            kind IS NOT NULL
            AND started_at IS NOT NULL
            AND ended_at IS NOT NULL
            AND active_seconds IS NOT NULL
            AND event_count IS NOT NULL
            AND first_event_id IS NOT NULL
        ) NOT VALID
        """
    )

    op.add_column("events", sa.Column("session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_events_session_id", "events", "sessions", ["session_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_session_id", "events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_events_session_id", table_name="events")
    op.drop_constraint("fk_events_session_id", "events", type_="foreignkey")
    op.drop_column("events", "session_id")

    op.drop_constraint("ck_sessions_complete", "sessions", type_="check")
    op.drop_constraint("ck_sessions_counts", "sessions", type_="check")
    op.drop_constraint("ck_sessions_time_order", "sessions", type_="check")
    op.drop_constraint("ck_sessions_kind", "sessions", type_="check")
    op.drop_index("ix_sessions_started_at", table_name="sessions")
    op.drop_constraint("uq_sessions_first_event_id", "sessions", type_="unique")
    op.drop_constraint("fk_sessions_first_event_id", "sessions", type_="foreignkey")
    for column in (
        "first_event_id", "event_count", "active_seconds",
        "ended_at", "started_at", "app", "kind",
    ):
        op.drop_column("sessions", column)
