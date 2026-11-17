"""Re-plan from the phone: a request table and two phone RPCs.

Revision ID: add_replan_request
Revises: plan_step_ready
Create Date: 2026-11-17

- replan_request: one row per tap on "Re-plan" (kind `replan`, with the person's note, maybe empty) or "Undo" (kind `undo`,
  `target` = the re-plan it undoes). The phone writes the row, then starts the GitHub job `replan`, which handles the newest
  pending request and writes back status, error and what it changed (`result`: removed and added blocks, used by Undo).
- request_replan(client_id, kind, note, target): stores a request (a retry with the same client_id is a no-op), returns its id.
- replan_status(): the newest few requests, so the phone can show "re-planning", "done, Undo" or the error.
Both SECURITY DEFINER, executable by `authenticated` only. Additive.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_replan_request"
down_revision: Union[str, Sequence[str], None] = "plan_step_ready"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = """
CREATE TABLE IF NOT EXISTS replan_request (
    id           serial PRIMARY KEY,
    client_id    uuid NOT NULL UNIQUE,
    kind         text NOT NULL CHECK (kind IN ('replan', 'undo')),
    note         text NOT NULL DEFAULT '',
    target       integer REFERENCES replan_request (id),
    requested_at timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'undone', 'superseded', 'expired')),
    error        text,
    result       jsonb NOT NULL DEFAULT '{}'::jsonb,
    finished_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_replan_request_pending ON replan_request (requested_at) WHERE status = 'pending';
"""

REQUEST = r"""
CREATE OR REPLACE FUNCTION public.request_replan(p_client_id uuid, p_kind text, p_note text, p_target integer)
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_id integer;
BEGIN
    IF p_kind NOT IN ('replan', 'undo') THEN RAISE EXCEPTION 'kind must be replan or undo'; END IF;
    IF p_kind = 'undo' AND p_target IS NULL THEN RAISE EXCEPTION 'undo needs the re-plan it undoes'; END IF;
    IF length(coalesce(p_note, '')) > 2000 THEN RAISE EXCEPTION 'note too long'; END IF;
    INSERT INTO replan_request (client_id, kind, note, target)
    VALUES (p_client_id, p_kind, coalesce(p_note, ''), CASE WHEN p_kind = 'undo' THEN p_target END)
    ON CONFLICT (client_id) DO NOTHING;
    SELECT id INTO v_id FROM replan_request WHERE client_id = p_client_id;
    RETURN v_id;
END $$;
"""

STATUS = r"""
CREATE OR REPLACE FUNCTION public.replan_status()
RETURNS TABLE (id integer, kind text, status text, note text, target integer, error text, requested_at timestamptz, finished_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT r.id, r.kind, r.status, r.note, r.target, r.error, r.requested_at, r.finished_at
      FROM replan_request r
     WHERE r.requested_at > now() - interval '2 days'
     ORDER BY r.id DESC
     LIMIT 5;
$$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.request_replan(uuid, text, text, integer) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.replan_status() FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.request_replan(uuid, text, text, integer) FROM anon;
        REVOKE ALL ON FUNCTION public.replan_status() FROM anon;
        REVOKE ALL ON TABLE replan_request FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE replan_request FROM authenticated;
        GRANT EXECUTE ON FUNCTION public.request_replan(uuid, text, text, integer) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.replan_status() TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(REQUEST)
    op.execute(STATUS)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.replan_status()")
    op.execute("DROP FUNCTION IF EXISTS public.request_replan(uuid, text, text, integer)")
    op.execute("DROP TABLE IF EXISTS replan_request")
