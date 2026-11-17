"""Task checks and debriefs as their own answer kinds; plan blocks carry their task link (planning design, phone).

Revision ID: add_answer_kinds
Revises: add_schedule_why
Create Date: 2026-11-17

- phone_answer.kind now also allows `task_check` (asked ~10 minutes before a task block) and `debrief` (asked after a
  deep-work block). The phone sends these as kind `tap` with `payload.subkind`, because the old upload_answers silently
  dropped unknown kinds. upload_answers now stores such a row under its real kind (and drops `subkind`), so phones on
  either side of this change keep working, and rows already stored that way are converted here.
- plan_blocks(day) also returns task_id and the task's slip_count, so the phone can tell task blocks apart and say
  "slipped Nx". Its result shape changes, so it is dropped and created again (and locked down again).
Downgrade turns the rows back into taps with a subkind and restores the old functions.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_answer_kinds"
down_revision: Union[str, Sequence[str], None] = "add_schedule_why"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_KINDS = "('distraction', 'sleep', 'evening', 'anomaly', 'tap', 'task_check', 'debrief')"
OLD_KINDS = "('distraction', 'sleep', 'evening', 'anomaly', 'tap')"

NEW_UPLOAD = r"""
CREATE OR REPLACE FUNCTION public.upload_answers(p_batch jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_new integer;
BEGIN
    IF jsonb_typeof(p_batch) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'batch must be a JSON array'; END IF;
    IF jsonb_array_length(p_batch) > 500 THEN RAISE EXCEPTION 'batch too large'; END IF;
    INSERT INTO phone_answer (client_id, kind, answered_at, day, payload)
    SELECT id, k, at, (at AT TIME ZONE 'Asia/Kolkata' - interval '5 hours')::date,   -- the 05:00-to-05:00 day, in IST
           CASE WHEN k <> raw_kind THEN payload - 'subkind' ELSE payload END
      FROM (
        SELECT (e->>'id')::uuid AS id, e->>'kind' AS raw_kind, (e->>'at')::timestamptz AS at,
               coalesce(e->'payload', '{}'::jsonb) AS payload,
               -- a tap that is really a task check or debrief (how the phone sends them) is stored as what it is
               CASE WHEN e->>'kind' = 'tap' AND e->'payload'->>'subkind' IN ('task_check', 'debrief')
                    THEN e->'payload'->>'subkind' ELSE e->>'kind' END AS k
          FROM jsonb_array_elements(p_batch) AS e
         WHERE coalesce(e->>'id', '') <> '' AND coalesce(e->>'at', '') <> ''
      ) r
     WHERE k IN """ + NEW_KINDS + r"""
    ON CONFLICT (client_id) DO NOTHING;
    GET DIAGNOSTICS v_new = ROW_COUNT;
    RETURN v_new;
END $$;
"""

OLD_UPLOAD = r"""
CREATE OR REPLACE FUNCTION public.upload_answers(p_batch jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_new integer;
BEGIN
    IF jsonb_typeof(p_batch) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'batch must be a JSON array'; END IF;
    IF jsonb_array_length(p_batch) > 500 THEN RAISE EXCEPTION 'batch too large'; END IF;
    INSERT INTO phone_answer (client_id, kind, answered_at, day, payload)
    SELECT (e->>'id')::uuid, e->>'kind', (e->>'at')::timestamptz,
           -- the 05:00-to-05:00 day, in IST
           ((e->>'at')::timestamptz AT TIME ZONE 'Asia/Kolkata' - interval '5 hours')::date,
           coalesce(e->'payload', '{}'::jsonb)
      FROM jsonb_array_elements(p_batch) AS e
     WHERE e->>'kind' IN """ + OLD_KINDS + r"""
       AND coalesce(e->>'id', '') <> '' AND coalesce(e->>'at', '') <> ''
    ON CONFLICT (client_id) DO NOTHING;
    GET DIAGNOSTICS v_new = ROW_COUNT;
    RETURN v_new;
END $$;
"""

NEW_PLAN_BLOCKS = r"""
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

OLD_PLAN_BLOCKS = r"""
CREATE FUNCTION public.plan_blocks(p_day date)
RETURNS TABLE (title text, start_at timestamptz, end_at timestamptz, origin text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT s.title, s.start_at, s.end_at, s.origin::text
      FROM schedule s
     WHERE s.deleted_at IS NULL
       AND (s.start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day
     ORDER BY s.start_at;
$$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.upload_answers(jsonb) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.upload_answers(jsonb) FROM anon;
        REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.upload_answers(jsonb) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.plan_blocks(date) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute("ALTER TABLE phone_answer DROP CONSTRAINT IF EXISTS phone_answer_kind_check")
    op.execute(f"ALTER TABLE phone_answer ADD CONSTRAINT phone_answer_kind_check CHECK (kind IN {NEW_KINDS})")
    op.execute("UPDATE phone_answer SET kind = payload->>'subkind', payload = payload - 'subkind' "
               "WHERE kind = 'tap' AND payload->>'subkind' IN ('task_check', 'debrief')")
    op.execute(NEW_UPLOAD)
    op.execute("DROP FUNCTION IF EXISTS public.plan_blocks(date)")
    op.execute(NEW_PLAN_BLOCKS)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute(OLD_UPLOAD)
    op.execute("DROP FUNCTION IF EXISTS public.plan_blocks(date)")
    op.execute(OLD_PLAN_BLOCKS)
    op.execute(LOCK_DOWN)
    op.execute("UPDATE phone_answer SET payload = payload || jsonb_build_object('subkind', kind), kind = 'tap' "
               "WHERE kind IN ('task_check', 'debrief')")
    op.execute("ALTER TABLE phone_answer DROP CONSTRAINT IF EXISTS phone_answer_kind_check")
    op.execute(f"ALTER TABLE phone_answer ADD CONSTRAINT phone_answer_kind_check CHECK (kind IN {OLD_KINDS})")
