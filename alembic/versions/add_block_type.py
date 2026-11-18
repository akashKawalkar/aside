"""The planner's block type on each calendar entry, for the phone.

Revision ID: add_block_type
Revises: add_task_question
Create Date: 2026-11-18

The planner labels every block it plans: kind (anchor, work or break) and, for work, mode (study, build, ...). That was thrown away
when the block became a calendar entry, so the phone had to guess what a block was from its title. Now the entry keeps it:
- schedule.plan_kind / schedule.plan_mode: set when a plan is accepted (and restored by an undo); empty for entries made by hand
  or pulled from Google Calendar, and for plans made before this migration.
- plan_blocks(day) also returns kind and mode. Its result shape changes, so it is dropped and created again (and locked down again).
The phone asks "what happened?" about phone use only during study or build blocks (kind work, mode study or build).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_block_type"
down_revision: Union[str, Sequence[str], None] = "add_task_question"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_PLAN_BLOCKS = r"""
CREATE FUNCTION public.plan_blocks(p_day date)
RETURNS TABLE (title text, start_at timestamptz, end_at timestamptz, origin text, task_id integer, slip_count integer, kind text, mode text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT s.title, s.start_at, s.end_at, s.origin::text, s.task_id, coalesce(t.slip_count, 0), s.plan_kind, s.plan_mode
      FROM schedule s
      LEFT JOIN tasks t ON t.id = s.task_id
     WHERE s.deleted_at IS NULL
       AND (s.start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day
     ORDER BY s.start_at;
$$;
"""

OLD_PLAN_BLOCKS = r"""
CREATE FUNCTION public.plan_blocks(p_day date)
RETURNS TABLE (title text, start_at timestamptz, end_at timestamptz, origin text, task_id integer, slip_count integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT s.title, s.start_at, s.end_at, s.origin::text, s.task_id, coalesce(t.slip_count, 0)
      FROM schedule s
      LEFT JOIN tasks t ON t.id = s.task_id
     WHERE s.deleted_at IS NULL
       AND (s.start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day
     ORDER BY s.start_at;
$$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.plan_blocks(date) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE schedule ADD COLUMN IF NOT EXISTS plan_kind text, ADD COLUMN IF NOT EXISTS plan_mode text")
    op.execute("DROP FUNCTION IF EXISTS public.plan_blocks(date)")
    op.execute(NEW_PLAN_BLOCKS)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.plan_blocks(date)")
    op.execute(OLD_PLAN_BLOCKS)
    op.execute(LOCK_DOWN)
    op.execute("ALTER TABLE schedule DROP COLUMN IF EXISTS plan_mode, DROP COLUMN IF EXISTS plan_kind")
