"""add schedule

Revision ID: c9e4a7d21f63
Revises: bdf723813a43
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e4a7d21f63"
down_revision: Union[str, Sequence[str], None] = "add_note_embedding_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "end_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_schedule_start_at",
        "schedule",
        ["start_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_start_at",
        table_name="schedule",
    )
    op.drop_table("schedule")