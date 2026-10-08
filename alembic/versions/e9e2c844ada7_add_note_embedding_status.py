"""add note embedding status

Revision ID: add_note_embedding_status
Revises: add_tasks
"""

from alembic import op


revision = "add_note_embedding_status"
down_revision = "add_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE notes
        ADD COLUMN IF NOT EXISTS embedding_status varchar(20)
        NOT NULL DEFAULT 'pending'
    """)

    op.execute("""
        ALTER TABLE notes
        ADD CONSTRAINT ck_notes_embedding_status
        CHECK (embedding_status IN ('pending', 'done', 'failed'))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE notes
        DROP CONSTRAINT IF EXISTS ck_notes_embedding_status
    """)

    op.execute("""
        ALTER TABLE notes
        DROP COLUMN IF EXISTS embedding_status
    """)