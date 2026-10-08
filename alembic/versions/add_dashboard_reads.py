"""Read-only dashboard (the GitHub Pages site): one function the signed-in page calls, one section at a time.

Revision ID: add_dashboard_reads
Revises: add_phone_watch
Create Date: 2026-10-09

dashboard(section, args) returns JSON for: overview, schedule, tasks, jobs, review, patterns, usage, uptime, drafts.
The page never touches tables (they have no grants for the public roles); this function runs as its owner, only reads,
and is executable by `authenticated` only. Notes are searched with the existing search_notes(). Additive only.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "add_dashboard_reads"
down_revision: Union[str, Sequence[str], None] = "add_phone_watch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DASHBOARD = r"""
CREATE OR REPLACE FUNCTION public.dashboard(p_section text, p_args jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
    v_today date := (now() AT TIME ZONE 'Asia/Kolkata')::date;
    v_from date;
    v_to date;
    v_days int;
    v jsonb;
BEGIN
    IF p_section = 'overview' THEN
        RETURN jsonb_build_object(
            'now', now(),
            'today', v_today,
            'tasks_pending', (SELECT count(*) FROM tasks WHERE status = 'pending'),
            'tasks_dropped', (SELECT count(*) FROM tasks WHERE dropped_at IS NOT NULL),
            'notes_total', (SELECT count(*) FROM notes),
            'notes_from_phone', (SELECT count(*) FROM notes WHERE source = 'phone'),
            'today_ready', schedule_ready(v_today),
            'tomorrow_ready', schedule_ready(v_today + 1),
            'phone_seconds_today', coalesce((SELECT sum(extract(epoch FROM ts_end - ts_start)) FROM events
                WHERE source = 'android' AND (ts_start AT TIME ZONE 'Asia/Kolkata')::date = v_today), 0),
            'laptop_minutes_today', (SELECT count(*) FROM heartbeat_log WHERE (ts AT TIME ZONE 'Asia/Kolkata')::date = v_today),
            'laptop_last_seen', (SELECT max(ts) FROM heartbeat_log),
            'phone_last_upload', (SELECT max(ingested_at) FROM events WHERE source = 'android'),
            'last_failure', (SELECT to_jsonb(f) FROM latest_failure() f),
            'review_date', (SELECT value ->> 'date' FROM review_state WHERE key = 'latest'),
            'schedule_today', coalesce((SELECT jsonb_agg(jsonb_build_object('title', title, 'start', start_at, 'end', end_at, 'origin', origin) ORDER BY start_at)
                FROM schedule WHERE deleted_at IS NULL AND (start_at AT TIME ZONE 'Asia/Kolkata')::date = v_today), '[]'::jsonb),
            'schedule_tomorrow', coalesce((SELECT jsonb_agg(jsonb_build_object('title', title, 'start', start_at, 'end', end_at, 'origin', origin) ORDER BY start_at)
                FROM schedule WHERE deleted_at IS NULL AND (start_at AT TIME ZONE 'Asia/Kolkata')::date = v_today + 1), '[]'::jsonb)
        );

    ELSIF p_section = 'schedule' THEN
        v_from := coalesce((p_args ->> 'from')::date, v_today);
        v_to := least(coalesce((p_args ->> 'to')::date, v_from + 6), v_from + 31);
        RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'title', title, 'start', start_at, 'end', end_at,
                   'origin', origin, 'edited', edited_by_user, 'task_id', task_id) ORDER BY start_at)
                FROM schedule WHERE deleted_at IS NULL
                 AND (start_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN v_from AND v_to), '[]'::jsonb);

    ELSIF p_section = 'tasks' THEN
        RETURN jsonb_build_object(
            'pending', coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'text', text, 'due', due_at, 'slips', slip_count, 'created', created_at)
                ORDER BY due_at NULLS LAST, created_at) FROM tasks WHERE status = 'pending'), '[]'::jsonb),
            'dropped', coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'text', text, 'due', due_at, 'dropped', dropped_at, 'reason', dropped_reason, 'slips', slip_count)
                ORDER BY dropped_at DESC) FROM (SELECT * FROM tasks WHERE dropped_at IS NOT NULL ORDER BY dropped_at DESC LIMIT 50) d), '[]'::jsonb),
            'completed', coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'text', text, 'completed', completed_at)
                ORDER BY completed_at DESC) FROM (SELECT * FROM tasks WHERE status = 'completed' ORDER BY completed_at DESC NULLS LAST LIMIT 30) c), '[]'::jsonb)
        );

    ELSIF p_section = 'jobs' THEN
        RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'at', coalesce(finished_at, started_at), 'kind', kind, 'step', step,
                   'day', day, 'ok', ok, 'attempt', attempt, 'error', error, 'skipped', detail ->> 'skipped') ORDER BY id DESC)
                FROM (SELECT * FROM job_run ORDER BY id DESC LIMIT 150) r), '[]'::jsonb);

    ELSIF p_section = 'review' THEN
        RETURN coalesce((SELECT value FROM review_state WHERE key = 'latest'), '{}'::jsonb);

    ELSIF p_section = 'patterns' THEN
        RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'kind', kind, 'text', text, 'status', status,
                   'occurrences', occurrences, 'misses', misses, 'first_seen', first_seen, 'last_seen', last_seen)
                   ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'questioned' THEN 1 ELSE 2 END, last_seen DESC)
                FROM observations WHERE deleted_at IS NULL), '[]'::jsonb);

    ELSIF p_section = 'usage' THEN
        v_days := least(greatest(coalesce((p_args ->> 'days')::int, 7), 1), 60);
        v_from := v_today - (v_days - 1);
        SELECT jsonb_build_object(
            'phone', coalesce((SELECT jsonb_agg(jsonb_build_object('day', day, 'app', app, 'seconds', secs) ORDER BY day, secs DESC) FROM (
                SELECT (e.ts_start AT TIME ZONE 'Asia/Kolkata')::date AS day, e.app, sum(extract(epoch FROM e.ts_end - e.ts_start))::int AS secs
                  FROM events e
                 WHERE e.source = 'android' AND e.kind = 'phone_app_focus' AND (e.ts_start AT TIME ZONE 'Asia/Kolkata')::date >= v_from
                   AND e.app IN (SELECT app FROM events WHERE source = 'android' AND (ts_start AT TIME ZONE 'Asia/Kolkata')::date >= v_from
                                  GROUP BY app ORDER BY sum(extract(epoch FROM ts_end - ts_start)) DESC LIMIT 15)
                 GROUP BY 1, 2) p), '[]'::jsonb),
            'laptop', coalesce((SELECT jsonb_agg(jsonb_build_object('day', day, 'app', app, 'seconds', secs) ORDER BY day, secs DESC) FROM (
                SELECT (e.ts_start AT TIME ZONE 'Asia/Kolkata')::date AS day, e.app, sum(extract(epoch FROM e.ts_end - e.ts_start))::int AS secs
                  FROM events e
                 WHERE e.source = 'laptop' AND e.kind = 'app_focus' AND (e.ts_start AT TIME ZONE 'Asia/Kolkata')::date >= v_from
                   AND e.app IN (SELECT app FROM events WHERE source = 'laptop' AND kind = 'app_focus' AND (ts_start AT TIME ZONE 'Asia/Kolkata')::date >= v_from
                                  GROUP BY app ORDER BY sum(extract(epoch FROM ts_end - ts_start)) DESC LIMIT 12)
                 GROUP BY 1, 2) l), '[]'::jsonb),
            'from', v_from, 'to', v_today
        ) INTO v;
        RETURN v;

    ELSIF p_section = 'uptime' THEN
        v_days := least(greatest(coalesce((p_args ->> 'days')::int, 14), 1), 60);
        v_from := v_today - (v_days - 1);
        RETURN jsonb_build_object('from', v_from, 'to', v_today, 'cells', coalesce((
            SELECT jsonb_agg(jsonb_build_object('day', day, 'hour', hour, 'beats', beats) ORDER BY day, hour) FROM (
                SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS day, extract(hour FROM ts AT TIME ZONE 'Asia/Kolkata')::int AS hour, count(*) AS beats
                  FROM heartbeat_log WHERE (ts AT TIME ZONE 'Asia/Kolkata')::date >= v_from GROUP BY 1, 2) u), '[]'::jsonb));

    ELSIF p_section = 'drafts' THEN
        RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('id', id, 'day', target_day, 'version', version, 'status', status, 'source', source,
                   'model', model, 'instruction', instruction, 'created', created_at,
                   'proposed', coalesce(jsonb_array_length(proposed), 0), 'entries', coalesce(jsonb_array_length(entries), 0)) ORDER BY id DESC)
                FROM (SELECT * FROM schedule_draft ORDER BY id DESC LIMIT 40) d), '[]'::jsonb);
    END IF;
    RAISE EXCEPTION 'unknown dashboard section %', p_section;
END $$;
"""

LOCK_DOWN = """
DO $$
BEGIN
    REVOKE ALL ON FUNCTION public.dashboard(text, jsonb) FROM PUBLIC;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.dashboard(text, jsonb) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        GRANT EXECUTE ON FUNCTION public.dashboard(text, jsonb) TO authenticated;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(DASHBOARD)
    op.execute(LOCK_DOWN)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.dashboard(text, jsonb)")
