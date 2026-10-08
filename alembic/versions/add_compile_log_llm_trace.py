"""add compile_log and llm_trace

Revision ID: add_compile_log_llm_trace
Revises: drop_labelling
Create Date: 2026-10-06

compile_log: one row per context compile (what was offered / chosen / dropped, tokens, recipe).
llm_trace:   one row per model call, shaped after the OpenTelemetry GenAI conventions.
Both only add tables; downgrade drops them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_compile_log_llm_trace"
down_revision: Union[str, Sequence[str], None] = "drop_labelling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compile_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("situation", sa.String(length=50), nullable=False),
        sa.Column("recipe_name", sa.String(length=50), nullable=False),
        sa.Column("recipe_hash", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("offered", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chosen", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dropped", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_compile_log_ts", "compile_log", ["ts"])

    op.create_table(
        "llm_trace",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("trace_id", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.String(length=16), nullable=True),
        sa.Column("operation", sa.String(length=50), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("finish_reason", sa.String(length=30), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("compile_log_id", sa.Integer(), sa.ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True),
        sa.Column("attrs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index("ix_llm_trace_ts", "llm_trace", ["ts"])
    op.create_index("ix_llm_trace_trace_id", "llm_trace", ["trace_id"])


def downgrade() -> None:
    op.drop_table("llm_trace")
    op.drop_table("compile_log")
