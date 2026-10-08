"""Phone usage upload (android plan, M5): the function the phone calls to send foreground-app sessions.

Revision ID: add_phone_usage
Revises: add_phone_rpcs
Create Date: 2026-10-09

upload_usage(batch jsonb): a JSON array of {key, app, start, end} (app = package name only, no titles). Each row becomes an
`events` row with source 'android' and kind 'phone_app_focus'. The kind is deliberately NOT 'app_focus', so the laptop
sessionizer (which reads kind IN ('app_focus','idle')) leaves phone rows alone until the review learns to use them.
Idempotent on the phone-made key. Returns how many rows were new. Additive only; same lock-down as add_phone_rpcs.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_phone_usage"
down_revision: Union[str, Sequence[str], None] = "add_phone_rpcs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPLOAD_USAGE = r"""
CREATE OR REPLACE FUNCTION public.upload_usage(p_batch jsonb) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_new integer;
BEGIN
    IF jsonb_typeof(p_batch) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'batch must be a JSON array'; END IF;
    IF jsonb_array_length(p_batch) > 1000 THEN RAISE EXCEPTION 'batch too large'; END IF;
    INSERT INTO events (source, kind, app, ts_start, ts_end, payload, dedupe_key)
    SELECT 'android', 'phone_app_focus', e->>'app', (e->>'start')::timestamptz, (e->>'end')::timestamptz,
           '{}'::jsonb, 'android:' || left(e->>'key', 56)
      FROM jsonb_array_elements(p_batch) AS e
     WHERE coalesce(e->>'app', '') <> '' AND coalesce(e->>'key', '') <> ''
       AND (e->>'end')::timestamptz >= (e->>'start')::timestamptz
    ON CONFLICT (dedupe_key) DO NOTHING;
    GET DIAGNOSTICS v_new = ROW_COUNT;
    RETURN v_new;
END $$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.upload_usage(jsonb) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.upload_usage(jsonb) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.upload_usage(jsonb) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(UPLOAD_USAGE)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.upload_usage(jsonb)")
