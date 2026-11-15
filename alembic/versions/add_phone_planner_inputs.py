"""Phone inputs for the planner: bubble/sheet answers, today's plan blocks, a sleep guess, `p:` and `r:` notes.

Revision ID: add_phone_planner_inputs
Revises: add_dashboard_reads
Create Date: 2026-11-15

- phone_answer + upload_answers(batch): everything the user answers or taps on the phone (distraction question, morning
  sleep sheet, evening sheet, anomaly question, receptivity taps). Idempotent on the id the phone makes for each answer.
- plan_blocks(day): the live schedule blocks of an IST day, read-only, for the phone's "Plan now" line.
- sleep_guess(day): the longest stretch of 3 hours or more with no input on any device between 18:00 the evening before
  and 14:00 on `day` (IST), so the morning sheet can prefill it. Null when there is none or it has not ended yet.
- anomaly_due(): yesterday's 05:00 day when both devices were nearly silent (laptop under 30 minutes on, phone under 30
  minutes in use) and the user has not yet answered the anomaly question for it; null otherwise. An all-day calendar label
  is not checked yet (the laptop sync does not store all-day events), so a labelled quiet day is asked about once.
- capture_item now also takes kinds `plan` (`p:` instruction for the next plan only) and `rule` (`r:` standing rule).
  They are notes tagged "plan" / "rule". They do not feed the statements extractor, they are not general context.
Additive. Same lock-down as the other phone functions.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_phone_planner_inputs"
down_revision: Union[str, Sequence[str], None] = "add_dashboard_reads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = """
CREATE TABLE IF NOT EXISTS phone_answer (
    id          serial PRIMARY KEY,
    client_id   uuid NOT NULL UNIQUE,
    kind        text NOT NULL CHECK (kind IN ('distraction', 'sleep', 'evening', 'anomaly', 'tap')),
    answered_at timestamptz NOT NULL,
    day         date NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_phone_answer_day ON phone_answer (day, kind);
"""

UPLOAD_ANSWERS = r"""
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
     WHERE e->>'kind' IN ('distraction', 'sleep', 'evening', 'anomaly', 'tap')
       AND coalesce(e->>'id', '') <> '' AND coalesce(e->>'at', '') <> ''
    ON CONFLICT (client_id) DO NOTHING;
    GET DIAGNOSTICS v_new = ROW_COUNT;
    RETURN v_new;
END $$;
"""

PLAN_BLOCKS = r"""
CREATE OR REPLACE FUNCTION public.plan_blocks(p_day date)
RETURNS TABLE (title text, start_at timestamptz, end_at timestamptz, origin text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT s.title, s.start_at, s.end_at, s.origin::text
      FROM schedule s
     WHERE s.deleted_at IS NULL
       AND (s.start_at AT TIME ZONE 'Asia/Kolkata')::date = p_day
     ORDER BY s.start_at;
$$;
"""

SLEEP_GUESS = r"""
CREATE OR REPLACE FUNCTION public.sleep_guess(p_day date)
RETURNS TABLE (sleep_start timestamptz, wake_at timestamptz, minutes integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    WITH win AS (
        SELECT ((p_day - 1)::timestamp + interval '18 hours') AT TIME ZONE 'Asia/Kolkata' AS w_start,
               (p_day::timestamp + interval '14 hours') AT TIME ZONE 'Asia/Kolkata' AS w_end
    ),
    act AS (
        SELECT greatest(e.ts_start, win.w_start) AS s, least(coalesce(e.ts_end, e.ts_start), win.w_end) AS e
          FROM events e, win
         WHERE e.kind <> 'idle'
           AND e.ts_start < win.w_end AND coalesce(e.ts_end, e.ts_start) > win.w_start
        UNION ALL SELECT w_start, w_start FROM win
        UNION ALL SELECT w_end, w_end FROM win
    ),
    seq AS (
        SELECT s, e, max(e) OVER (ORDER BY s, e ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
          FROM act
    )
    SELECT prev_end, s, (extract(epoch FROM s - prev_end) / 60)::int
      FROM seq, win
     WHERE prev_end IS NOT NULL
       AND s - prev_end >= interval '3 hours'
       AND s < win.w_end                       -- the stretch has ended: there was activity after it
     ORDER BY s - prev_end DESC
     LIMIT 1;
$$;
"""

ANOMALY_DUE = r"""
CREATE OR REPLACE FUNCTION public.anomaly_due() RETURNS date
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    WITH w AS (
        SELECT d AS day,
               (d::timestamp + interval '5 hours') AT TIME ZONE 'Asia/Kolkata' AS s,
               ((d + 1)::timestamp + interval '5 hours') AT TIME ZONE 'Asia/Kolkata' AS e
          FROM (SELECT ((now() AT TIME ZONE 'Asia/Kolkata') - interval '5 hours')::date - 1 AS d) x
    )
    SELECT w.day FROM w
     WHERE (SELECT count(*) FROM heartbeat_log h WHERE h.ts >= w.s AND h.ts < w.e) < 30
       AND coalesce((SELECT sum(extract(epoch FROM least(ev.ts_end, w.e) - greatest(ev.ts_start, w.s)))
                       FROM events ev WHERE ev.source = 'android' AND ev.ts_start < w.e AND ev.ts_end > w.s), 0) < 1800
       AND EXISTS (SELECT 1 FROM events ev WHERE ev.ts_start < w.s)      -- there was data before that day at all
       AND NOT EXISTS (SELECT 1 FROM phone_answer a WHERE a.kind = 'anomaly' AND (a.payload->>'for_day')::date = w.day);
$$;
"""

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
    IF p_kind NOT IN ('note', 'journal', 'task', 'plan', 'rule') THEN RAISE EXCEPTION 'unknown kind %', p_kind; END IF;
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

# search_notes reports the kind of a note; teach it the two new tags (same signature, so a plain replace).
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
           CASE WHEN n.tags ? 'journal' THEN 'journal'
                WHEN n.tags ? 'plan' THEN 'plan'
                WHEN n.tags ? 'rule' THEN 'rule'
                ELSE 'note' END,
           n.text, n.created_at
      FROM notes n
     WHERE cardinality(v_patterns) = 0 OR lower(n.text) LIKE ANY (v_patterns)
     ORDER BY n.created_at DESC
     LIMIT least(greatest(coalesce(p_limit, 50), 1), 200);
END $$;
"""

# The previous bodies, put back on downgrade.
OLD_CAPTURE_ITEM = r"""
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
        DELETE FROM notes WHERE client_id = p_client_id;
        INSERT INTO tasks (text, due_at, status, created_at, client_id)
        VALUES (v_text, coalesce(p_due_at, v_created + interval '24 hours'), 'pending', v_created, p_client_id)
        ON CONFLICT (client_id) DO UPDATE
            SET text = EXCLUDED.text, due_at = coalesce(p_due_at, tasks.due_at);
    ELSE
        DELETE FROM tasks WHERE client_id = p_client_id;
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

OLD_SEARCH_NOTES = SEARCH_NOTES.replace(
    """CASE WHEN n.tags ? 'journal' THEN 'journal'
                WHEN n.tags ? 'plan' THEN 'plan'
                WHEN n.tags ? 'rule' THEN 'rule'
                ELSE 'note' END""",
    "CASE WHEN n.tags ? 'journal' THEN 'journal' ELSE 'note' END",
)

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.upload_answers(jsonb) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.sleep_guess(date) FROM PUBLIC;
    REVOKE ALL ON FUNCTION public.anomaly_due() FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.upload_answers(jsonb) FROM anon;
        REVOKE ALL ON FUNCTION public.plan_blocks(date) FROM anon;
        REVOKE ALL ON FUNCTION public.sleep_guess(date) FROM anon;
        REVOKE ALL ON FUNCTION public.anomaly_due() FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.upload_answers(jsonb) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.plan_blocks(date) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.sleep_guess(date) TO authenticated;
        GRANT EXECUTE ON FUNCTION public.anomaly_due() TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(TABLE)
    op.execute(UPLOAD_ANSWERS)
    op.execute(PLAN_BLOCKS)
    op.execute(SLEEP_GUESS)
    op.execute(ANOMALY_DUE)
    op.execute(CAPTURE_ITEM)
    op.execute(SEARCH_NOTES)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute(OLD_SEARCH_NOTES)
    op.execute(OLD_CAPTURE_ITEM)
    op.execute("DROP FUNCTION IF EXISTS public.anomaly_due()")
    op.execute("DROP FUNCTION IF EXISTS public.sleep_guess(date)")
    op.execute("DROP FUNCTION IF EXISTS public.plan_blocks(date)")
    op.execute("DROP FUNCTION IF EXISTS public.upload_answers(jsonb)")
    op.execute("DROP TABLE IF EXISTS phone_answer")
