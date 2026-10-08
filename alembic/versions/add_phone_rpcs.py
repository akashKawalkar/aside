"""Phone app support (android plan, section 3): client ids + the functions the phone calls.

Revision ID: add_phone_rpcs
Revises: add_cloud_support
Create Date: 2026-10-09

The phone never touches tables. It signs in (Supabase Auth, sign-ups off) and calls these functions, each of which
is idempotent on the client id the phone makes for every item, so a retry can never duplicate:
- capture_item(client_id, kind, text, due_at, created_at): a note, journal entry or task. Used for new items AND for
  edits and note<->task conversions (the row in the other table is removed). A first journal/note also writes the
  `statements` row the nightly extractor reads, exactly like the laptop's /input route.
- delete_item(client_id): removes the note or task.
- search_notes(query, limit): any-word, case-insensitive, newest first; includes notes written on the laptop.

Additive only. The functions run as their owner (SECURITY DEFINER), so RLS does not get in their way; execute is
revoked from public/anon and granted to `authenticated` when that role exists (it does on Supabase, not locally).
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql
import sqlalchemy as sa

revision: str = "add_phone_rpcs"
down_revision: Union[str, Sequence[str], None] = "add_cloud_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CAPTURE_ITEM = r"""
CREATE OR REPLACE FUNCTION public.capture_item(
    p_client_id uuid, p_kind text, p_text text, p_due_at timestamptz, p_created_at timestamptz
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_text text := btrim(coalesce(p_text, ''));
    v_created timestamptz := coalesce(p_created_at, now());
    v_new boolean;
BEGIN
    IF p_client_id IS NULL THEN RAISE EXCEPTION 'client_id is required'; END IF;
    IF p_kind NOT IN ('note', 'journal', 'task') THEN RAISE EXCEPTION 'unknown kind %', p_kind; END IF;
    IF v_text = '' THEN RAISE EXCEPTION 'text is empty'; END IF;

    IF p_kind = 'task' THEN
        DELETE FROM notes WHERE client_id = p_client_id;            -- it was a note before the edit
        INSERT INTO tasks (text, due_at, status, created_at, client_id)
        VALUES (v_text, coalesce(p_due_at, v_created + interval '24 hours'), 'pending', v_created, p_client_id)
        ON CONFLICT (client_id) DO UPDATE
            SET text = EXCLUDED.text, due_at = coalesce(p_due_at, tasks.due_at);
    ELSE
        DELETE FROM tasks WHERE client_id = p_client_id;            -- it was a task before the edit
        INSERT INTO notes (text, tags, source, created_at, embedding_status, client_id)
        VALUES (
            v_text,
            CASE WHEN p_kind = 'journal' THEN '["journal"]'::jsonb ELSE '[]'::jsonb END,
            'phone', v_created, 'pending', p_client_id
        )
        ON CONFLICT (client_id) DO UPDATE
            SET text = EXCLUDED.text, tags = EXCLUDED.tags,
                embedding = NULL, embedding_model = NULL, embedding_status = 'pending'
            WHERE notes.text IS DISTINCT FROM EXCLUDED.text OR notes.tags IS DISTINCT FROM EXCLUDED.tags
        RETURNING (xmax = 0) INTO v_new;
        IF v_new THEN
            INSERT INTO statements (text, source, created_at)
            VALUES (v_text, CASE WHEN p_kind = 'journal' THEN 'journal' ELSE 'note' END, v_created);
        END IF;
    END IF;
END $$;
"""

DELETE_ITEM = r"""
CREATE OR REPLACE FUNCTION public.delete_item(p_client_id uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    DELETE FROM notes WHERE client_id = p_client_id;
    DELETE FROM tasks WHERE client_id = p_client_id;
$$;
"""

SEARCH_NOTES = r"""
CREATE OR REPLACE FUNCTION public.search_notes(p_query text, p_limit int DEFAULT 50)
RETURNS TABLE (client_id uuid, id int, kind text, text text, created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_patterns text[];
BEGIN
    SELECT coalesce(array_agg('%' || replace(replace(replace(w, '\', '\\'), '%', '\%'), '_', '\_') || '%'), '{}')
      INTO v_patterns
      FROM unnest(regexp_split_to_array(lower(btrim(coalesce(p_query, ''))), '\s+')) AS w
     WHERE w <> '';
    RETURN QUERY
    SELECT n.client_id, n.id,
           CASE WHEN n.tags ? 'journal' THEN 'journal' ELSE 'note' END,
           n.text, n.created_at
      FROM notes n
     WHERE cardinality(v_patterns) = 0 OR lower(n.text) LIKE ANY (v_patterns)
     ORDER BY n.created_at DESC
     LIMIT least(greatest(coalesce(p_limit, 50), 1), 200);
END $$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.capture_item(uuid, text, text, timestamptz, timestamptz) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.delete_item(uuid) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.search_notes(text, int) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.capture_item(uuid, text, text, timestamptz, timestamptz) FROM anon;
        REVOKE ALL ON FUNCTION public.delete_item(uuid) FROM anon;
        REVOKE ALL ON FUNCTION public.search_notes(text, int) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.capture_item(uuid, text, text, timestamptz, timestamptz) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.delete_item(uuid) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.search_notes(text, int) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.add_column("notes", sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("tasks", sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_notes_client_id", "notes", ["client_id"])
    op.create_unique_constraint("uq_tasks_client_id", "tasks", ["client_id"])
    op.execute(CAPTURE_ITEM)
    op.execute(DELETE_ITEM)
    op.execute(SEARCH_NOTES)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.search_notes(text, int)")
    op.execute("DROP FUNCTION IF EXISTS public.delete_item(uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.capture_item(uuid, text, text, timestamptz, timestamptz)")
    op.drop_constraint("uq_tasks_client_id", "tasks", type_="unique")
    op.drop_constraint("uq_notes_client_id", "notes", type_="unique")
    op.drop_column("tasks", "client_id")
    op.drop_column("notes", "client_id")
