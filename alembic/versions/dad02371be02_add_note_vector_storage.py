"""add note vector storage

Revision ID: add_note_vector_storage
Revises: fix_missing_storage_tables
"""

from alembic import op


revision = "add_note_vector_storage"
down_revision = "fix_missing_storage_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        ALTER TABLE notes
        ADD COLUMN IF NOT EXISTS embedding vector(384)
    """)

    op.execute("""
        ALTER TABLE notes
        ADD COLUMN IF NOT EXISTS embedding_model varchar(200)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_notes_embedding_hnsw
        ON notes
        USING hnsw (embedding vector_cosine_ops)
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS ix_notes_embedding_hnsw
    """)

    op.execute("""
        ALTER TABLE notes
        DROP COLUMN IF EXISTS embedding_model
    """)

    op.execute("""
        ALTER TABLE notes
        DROP COLUMN IF EXISTS embedding
    """)