"""Phone watchdog and failure notice (android plan, M6): two read-only questions the phone asks the database.

Revision ID: add_phone_watch
Revises: add_phone_usage
Create Date: 2026-10-09

- schedule_ready(day): is there a schedule for that (IST) day? True when the nightly job's `generate` step succeeded for
  the day (including "skipped because the day already has entries") or the day has any live schedule entry.
- latest_failure(): the newest step whose LAST recorded attempt failed, once it is at least 10 minutes old (so the
  in-run retry has had its chance) and no later success exists. The phone shows it once.
Both are read-only, SECURITY DEFINER, and executable by `authenticated` only. Additive.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_phone_watch"
down_revision: Union[str, Sequence[str], None] = "add_phone_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEDULE_READY = r"""
CREATE OR REPLACE FUNCTION public.schedule_ready(p_day date) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM job_run WHERE day = p_day AND step = 'generate' AND ok)
        OR EXISTS (SELECT 1 FROM schedule WHERE deleted_at IS NULL AND (start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day);
$$;
"""

LATEST_FAILURE = r"""
CREATE OR REPLACE FUNCTION public.latest_failure()
RETURNS TABLE (id int, day date, step text, kind text, error text, at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT j.id, j.day, j.step::text, j.kind::text, j.error, j.finished_at
      FROM job_run j
     WHERE NOT j.ok
       AND j.finished_at < now() - interval '10 minutes'
       AND j.finished_at > now() - interval '3 days'
       AND j.id = (SELECT max(k.id) FROM job_run k WHERE k.day IS NOT DISTINCT FROM j.day AND k.step = j.step)
     ORDER BY j.id DESC
     LIMIT 1;
$$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.schedule_ready(date) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.latest_failure() FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.schedule_ready(date) FROM anon;
        REVOKE ALL ON FUNCTION public.latest_failure() FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.schedule_ready(date) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.latest_failure() TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(SCHEDULE_READY)
    op.execute(LATEST_FAILURE)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.latest_failure()")
    op.execute("DROP FUNCTION IF EXISTS public.schedule_ready(date)")
