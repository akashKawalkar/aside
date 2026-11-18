"""Slipping-task questions: a question table, a "someday" flag on tasks, and two phone RPCs.

Revision ID: add_task_question
Revises: phone_task_status
Create Date: 2026-11-18

A task that was planned and not done (or pushed) three days running gets one question the next morning: keep it, make it smaller,
drop it, or park it as "someday". The nightly job writes the question (practiced/slipping.py); the phone shows it with its glow and
answers it through answer_task_question, which applies the answer at once (no GitHub job in between).
- tasks.someday_at: kept but not planned. The planner, the schedule generator and the stale-task drop skip tasks that have it.
- task_question: one row per question. A task is asked at most once a week (the nightly job checks created_at).
- task_questions(): the open questions for tasks that are still open, for the phone.
- answer_task_question(id, choice, text): keep = nothing changes; smaller = the typed first step replaces the task's text and
  its due date moves to tomorrow (the original text is kept on the question row); drop = the task is dropped (the next sync removes
  it from Google Tasks); someday = someday_at is set. A question already answered or expired is left as it is, so a retry is safe.
Both RPCs SECURITY DEFINER, executable by `authenticated` only. Additive.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_task_question"
down_revision: Union[str, Sequence[str], None] = "phone_task_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = """
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS someday_at timestamptz;

CREATE TABLE IF NOT EXISTS task_question (
    id            serial PRIMARY KEY,
    task_id       integer NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    slipped_days  integer NOT NULL,
    for_day       date NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'expired')),
    choice        text CHECK (choice IN ('keep', 'smaller', 'drop', 'someday')),
    answer_text   text,
    original_text text,
    answered_at   timestamptz
);
CREATE INDEX IF NOT EXISTS ix_task_question_task ON task_question (task_id, created_at);
CREATE INDEX IF NOT EXISTS ix_task_question_pending ON task_question (created_at) WHERE status = 'pending';
"""

LIST = r"""
CREATE OR REPLACE FUNCTION public.task_questions()
RETURNS TABLE (id integer, task_id integer, task_text text, slipped_days integer, for_day date, created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT q.id, q.task_id, t.text, q.slipped_days, q.for_day, q.created_at
      FROM task_question q JOIN tasks t ON t.id = q.task_id
     WHERE q.status = 'pending' AND q.created_at > now() - interval '3 days' AND t.status = 'pending'
     ORDER BY q.id
     LIMIT 5;
$$;
"""

ANSWER = r"""
CREATE OR REPLACE FUNCTION public.answer_task_question(p_id integer, p_choice text, p_text text)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
    q task_question%ROWTYPE;
    v_text text := btrim(coalesce(p_text, ''));
BEGIN
    IF p_choice NOT IN ('keep', 'smaller', 'drop', 'someday') THEN RAISE EXCEPTION 'unknown choice %', p_choice; END IF;
    IF p_choice = 'smaller' AND (v_text = '' OR length(v_text) > 300) THEN
        RAISE EXCEPTION 'a smaller first step needs 1 to 300 characters';
    END IF;
    SELECT * INTO q FROM task_question WHERE id = p_id FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'no such question'; END IF;
    IF q.status <> 'pending' THEN RETURN q.status; END IF;

    IF p_choice = 'smaller' THEN
        UPDATE task_question SET original_text = (SELECT text FROM tasks WHERE id = q.task_id) WHERE id = q.id;
        UPDATE tasks SET text = v_text, due_at = now() + interval '24 hours', slip_count = 0, someday_at = NULL
         WHERE id = q.task_id AND status = 'pending';
    ELSIF p_choice = 'drop' THEN
        UPDATE tasks SET status = 'dropped', dropped_at = now(), dropped_reason = 'dropped from the slipping-task question'
         WHERE id = q.task_id AND status = 'pending';
    ELSIF p_choice = 'someday' THEN
        UPDATE tasks SET someday_at = now() WHERE id = q.task_id AND status = 'pending';
    END IF;
    UPDATE task_question
       SET status = 'answered', choice = p_choice, answer_text = CASE WHEN p_choice = 'smaller' THEN v_text END, answered_at = now()
     WHERE id = q.id;
    RETURN 'answered';
END $$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.task_questions() FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.answer_task_question(integer, text, text) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.task_questions() FROM anon;
        REVOKE ALL ON FUNCTION public.answer_task_question(integer, text, text) FROM anon;
        REVOKE ALL ON TABLE task_question FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE task_question FROM authenticated;
        GRANT EXECUTE ON FUNCTION public.task_questions() TO authenticated;
        GRANT EXECUTE ON FUNCTION public.answer_task_question(integer, text, text) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(LIST)
    op.execute(ANSWER)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.answer_task_question(integer, text, text)")
    op.execute("DROP FUNCTION IF EXISTS public.task_questions()")
    op.execute("DROP TABLE IF EXISTS task_question")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS someday_at")
