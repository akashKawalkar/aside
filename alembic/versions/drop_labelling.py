"""drop labelling tables and the placeholder session

Revision ID: drop_labelling
Revises: add_session_fields
Create Date: 2026-10-06

The labelling tool was dropped from the project. This removes its tables
(labels, predictions, feedback: one smoke-test row each) and the one
placeholder session they hung off, then validates ck_sessions_complete so
every session row must now be complete.

downgrade() recreates the three tables empty; the data itself lives in the
pg_dump taken before this migration (backups/).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "drop_labelling"
down_revision: Union[str, Sequence[str], None] = "add_session_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("predictions")
    op.drop_table("labels")

    # Only the legacy placeholder (no kind / start / end) is removed; a real
    # session always has them.
    op.execute(
        """
        DELETE FROM sessions
        WHERE kind IS NULL
          AND started_at IS NULL
          AND ended_at IS NULL
        """
    )
    op.execute("ALTER TABLE sessions VALIDATE CONSTRAINT ck_sessions_complete")


def downgrade() -> None:
    op.create_table(
        "labels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_labels_session_id", "labels", ["session_id"])

    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("prediction", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_predictions_session_id", "predictions", ["session_id"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prediction_id", sa.Integer(), sa.ForeignKey("predictions.id"), nullable=False),
        sa.Column("label_id", sa.Integer(), sa.ForeignKey("labels.id"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_feedback_prediction_id", "feedback", ["prediction_id"])
    op.create_index("ix_feedback_label_id", "feedback", ["label_id"])
