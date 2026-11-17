"""schedule_ready(day) knows the new `plan` step, and a day with only the person's own events is not "ready".

Revision ID: plan_step_ready
Revises: add_answer_kinds
Create Date: 2026-11-17

The nightly job's `generate` step became `plan` (practiced/planrun.py), so schedule_ready only saw a plan through the
"any schedule row" clause, and that clause was true for a day holding nothing but a dentist appointment pulled from
Google Calendar: the phone's morning check then never started the retry. Now a day is ready when a `plan` (or old
`generate`) step succeeded for it, including "skipped, already planned", or it has generated blocks. Same signature and
grants, so CREATE OR REPLACE keeps the lock-down from add_phone_watch.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "plan_step_ready"
down_revision: Union[str, Sequence[str], None] = "add_answer_kinds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW = r"""
CREATE OR REPLACE FUNCTION public.schedule_ready(p_day date) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM job_run WHERE day = p_day AND step IN ('plan', 'generate') AND ok)
        OR EXISTS (SELECT 1 FROM schedule WHERE deleted_at IS NULL AND origin = 'generated'
                     AND (start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day);
$$;
"""

OLD = r"""
CREATE OR REPLACE FUNCTION public.schedule_ready(p_day date) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM job_run WHERE day = p_day AND step = 'generate' AND ok)
        OR EXISTS (SELECT 1 FROM schedule WHERE deleted_at IS NULL AND (start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day);
$$;
"""


def upgrade() -> None:
    op.execute(NEW)


def downgrade() -> None:
    op.execute(OLD)
