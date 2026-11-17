"""Phone tasks: a delete drops instead of erasing, and the phone can see which of its tasks are done.

Revision ID: phone_task_status
Revises: add_replan_request
Create Date: 2026-11-17

Before, deleting a task in the app (or turning it into a note) erased the task row. The Google Tasks link went with it, so an
open task came straight back from Google Tasks at the next sync, and a completed one lost its "done" from the history.
- delete_item: a pending task is dropped ("deleted on the phone"; the next sync removes it from Google Tasks); a completed or
  already dropped task is left as it is. Notes are still deleted.
- capture_item: a task turned into a note is dropped the same way ("changed to a note on the phone"); turned back into a task,
  it is pending again, with its Google link cleared so it is pushed as a fresh Google task.
- task_status(client_ids): status and when it closed, for the phone's own tasks.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "phone_task_status"
down_revision: Union[str, Sequence[str], None] = "add_replan_request"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TO_NOTE = "changed to a note on the phone"

CAPTURE_TEMPLATE = r"""
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
    IF p_kind NOT IN ('note', 'journal', 'task', 'plan', 'rule') THEN RAISE EXCEPTION 'unknown kind %', p_kind; END IF;
    IF v_text = '' THEN RAISE EXCEPTION 'text is empty'; END IF;

    IF p_kind = 'task' THEN
        DELETE FROM notes WHERE client_id = p_client_id;            -- it was a note before the edit
        INSERT INTO tasks (text, due_at, status, created_at, client_id)
        VALUES (v_text, coalesce(p_due_at, v_created + interval '24 hours'), 'pending', v_created, p_client_id)
        ON CONFLICT (client_id) DO UPDATE
            SET text = EXCLUDED.text, due_at = coalesce(p_due_at, tasks.due_at)__REVIVE__;
    ELSE
        __TASK_GONE__
        INSERT INTO notes (text, tags, source, created_at, embedding_status, client_id)
        VALUES (
            v_text,
            CASE p_kind WHEN 'journal' THEN '["journal"]'::jsonb
                        WHEN 'plan' THEN '["plan"]'::jsonb
                        WHEN 'rule' THEN '["rule"]'::jsonb
                        ELSE '[]'::jsonb END,
            'phone', v_created, 'pending', p_client_id
        )
        ON CONFLICT (client_id) DO UPDATE
            SET text = EXCLUDED.text, tags = EXCLUDED.tags,
                embedding = NULL, embedding_model = NULL, embedding_status = 'pending'
            WHERE notes.text IS DISTINCT FROM EXCLUDED.text OR notes.tags IS DISTINCT FROM EXCLUDED.tags
        RETURNING (xmax = 0) INTO v_new;
        IF v_new AND p_kind IN ('note', 'journal') THEN
            INSERT INTO statements (text, source, created_at)
            VALUES (v_text, CASE WHEN p_kind = 'journal' THEN 'journal' ELSE 'note' END, v_created);
        END IF;
    END IF;
END $$;
"""

REVIVE = f""",
                -- dropped only because it was turned into a note: pending again, pushed to Google Tasks afresh
                status = CASE WHEN tasks.dropped_reason = '{TO_NOTE}' THEN 'pending' ELSE tasks.status END,
                gtask_id = CASE WHEN tasks.dropped_reason = '{TO_NOTE}' THEN NULL ELSE tasks.gtask_id END,
                gtask_updated = CASE WHEN tasks.dropped_reason = '{TO_NOTE}' THEN NULL ELSE tasks.gtask_updated END,
                dropped_at = CASE WHEN tasks.dropped_reason = '{TO_NOTE}' THEN NULL ELSE tasks.dropped_at END,
                dropped_reason = CASE WHEN tasks.dropped_reason = '{TO_NOTE}' THEN NULL ELSE tasks.dropped_reason END"""

NEW_CAPTURE = (CAPTURE_TEMPLATE.replace("__REVIVE__", REVIVE).replace(
    "__TASK_GONE__",
    f"UPDATE tasks SET status = 'dropped', dropped_at = now(), dropped_reason = '{TO_NOTE}'\n"
    "         WHERE client_id = p_client_id AND status = 'pending';      -- it was a task before the edit"))
OLD_CAPTURE = (CAPTURE_TEMPLATE.replace("__REVIVE__", "").replace(
    "__TASK_GONE__", "DELETE FROM tasks WHERE client_id = p_client_id;            -- it was a task before the edit"))

NEW_DELETE = r"""
CREATE OR REPLACE FUNCTION public.delete_item(p_client_id uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    DELETE FROM notes WHERE client_id = p_client_id;
    -- an open task is dropped (the next sync removes it from Google Tasks); a finished one keeps its history
    UPDATE tasks SET status = 'dropped', dropped_at = now(), dropped_reason = 'deleted on the phone'
     WHERE client_id = p_client_id AND status = 'pending';
$$;
"""

OLD_DELETE = r"""
CREATE OR REPLACE FUNCTION public.delete_item(p_client_id uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    DELETE FROM notes WHERE client_id = p_client_id;
    DELETE FROM tasks WHERE client_id = p_client_id;
$$;
"""

TASK_STATUS = r"""
CREATE OR REPLACE FUNCTION public.task_status(p_client_ids uuid[])
RETURNS TABLE (client_id uuid, status text, closed_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT t.client_id, t.status::text, CASE t.status WHEN 'completed' THEN t.completed_at WHEN 'dropped' THEN t.dropped_at END
      FROM tasks t
     WHERE t.client_id = ANY (p_client_ids[1:500]);
$$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.task_status(uuid[]) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.task_status(uuid[]) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.task_status(uuid[]) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(NEW_CAPTURE)
    op.execute(NEW_DELETE)
    op.execute(TASK_STATUS)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.task_status(uuid[])")
    op.execute(OLD_DELETE)
    op.execute(OLD_CAPTURE)
