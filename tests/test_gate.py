from __future__ import annotations

import pytest

from context.gate import UnsafeSQL, gate_rows, gate_text, guard_sql
from llm.tokens import estimate_tokens


# ---------- gate_text ----------

def test_short_text_passes_through_untouched():
    assert gate_text("hello", 100) == "hello"
    assert gate_text("", 5) == ""


def test_long_text_is_cut_to_the_budget_and_says_so():
    out = gate_text("word " * 2000, 50)
    assert estimate_tokens(out) <= 50                       # the note counts against the budget
    assert out.startswith("word word") and "truncated" in out
    assert "more characters" in out


def test_a_budget_too_small_for_anything_still_returns_a_note_not_an_error():
    out = gate_text("x" * 500, 1)
    assert "truncated" in out and "x" * 50 not in out


# ---------- gate_rows ----------

ROWS = [{"id": i, "title": f"task {i}", "secret": "s" * 30, "note": None} for i in range(1, 101)]


def test_rows_that_fit_are_shown_in_full_with_no_note():
    out = gate_rows(ROWS[:3], 1000)
    assert "task 1" in out and "task 3" in out
    assert "narrow the query" not in out and "more row" not in out


def test_unused_columns_are_dropped_and_order_follows_keep_columns():
    out = gate_rows(ROWS[:2], 1000, keep_columns=["title", "id"])
    header, first, *_ = out.splitlines()
    assert header.split(" | ") == ["title", "id"]
    assert first.split(" | ") == ["task 1", "1"]
    assert "secret" not in out and "sss" not in out


def test_too_many_rows_are_truncated_with_a_pointer_note():
    out = gate_rows(ROWS, 120)
    assert estimate_tokens(out) <= 120
    shown = out.count("task ")
    assert 0 < shown < 100
    assert f"{100 - shown} more rows, narrow the query" in out


def test_one_extra_row_is_singular():
    for budget in range(40, 400):
        out = gate_rows(ROWS[:6], budget)
        if "1 more row," in out:
            assert "1 more rows" not in out
            return
    pytest.fail("never produced a one-row remainder")


def test_none_becomes_empty_and_long_cells_are_clipped():
    out = gate_rows([{"a": None, "b": "y" * 1000}], 5000)
    first = out.splitlines()[1]
    assert first.startswith(" | ") and len(first) < 400 and first.endswith("…")


def test_no_rows():
    assert gate_rows([], 100) == "(no rows)"


def test_rows_missing_a_column_get_a_blank():
    out = gate_rows([{"a": 1, "b": 2}, {"a": 3}], 1000)
    assert out.splitlines()[2] == "3 | "


# ---------- guard_sql: what is allowed ----------

@pytest.mark.parametrize("sql", [
    "SELECT id, title FROM tasks",
    "select * from tasks where status = 'pending' order by id",
    "  SELECT 1;  ",
    "WITH recent AS (SELECT * FROM notes ORDER BY id DESC LIMIT 5) SELECT * FROM recent",
    "SELECT 'a;b' AS semi",                                   # a semicolon inside a string is not a second statement
    "SELECT 'it''s' AS quote",
    'SELECT "update" FROM t',                                 # a quoted identifier is not the keyword
    "SELECT settings, doc FROM tasks",                        # `set`/`do` inside other words
    "SELECT a FROM t UNION SELECT b FROM u",
    "SELECT count(*) FROM (SELECT id FROM tasks LIMIT 5000) s",   # a subquery limit is not the outer one
])
def test_read_only_queries_are_accepted(sql):
    out = guard_sql(sql)
    assert out.upper().startswith(("SELECT", "WITH")) and not out.endswith(";")


# ---------- guard_sql: what is refused ----------

@pytest.mark.parametrize("sql", [
    "",
    "   ",
    "DELETE FROM tasks",
    "UPDATE tasks SET text = 'x'",
    "INSERT INTO tasks (text) VALUES ('x')",
    "DROP TABLE tasks",
    "TRUNCATE tasks",
    "CREATE TABLE x (a int)",
    "ALTER TABLE tasks ADD COLUMN x int",
    "GRANT ALL ON tasks TO public",
    "COPY tasks TO '/tmp/x'",
    "SET ROLE postgres",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE tasks",
    "SELECT 1 -- ; DROP TABLE tasks",
    "SELECT 1 /* hidden */",
    "SELECT * INTO backup FROM tasks",
    "SELECT * FROM tasks FOR UPDATE",
    "SELECT * FROM tasks FOR SHARE",
    "WITH d AS (DELETE FROM tasks RETURNING *) SELECT * FROM d",
    "WITH u AS (UPDATE tasks SET text = 'x' RETURNING *) SELECT * FROM u",
    "SELECT pg_sleep(100)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM pg_authid",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT dblink('host=x', 'select 1')",
    "SELECT set_config('role', 'postgres', false)",
    "SELECT nextval('tasks_id_seq')",
    "SELECT query_to_xml('delete from tasks', true, true, '')",
    "SELECT $$x$$",
    "SELECT $tag$x$tag$",
    "SELECT E'a\\'; DROP TABLE tasks; --'",
    "SELECT 'unterminated",
    'SELECT "unterminated',
    "(SELECT 1)",
    "EXPLAIN ANALYZE SELECT 1",
    "SELECT * FROM tasks FETCH FIRST 5 ROWS ONLY",
    "SELECT * FROM tasks LIMIT (SELECT 5)",
])
def test_everything_else_is_refused(sql):
    with pytest.raises(UnsafeSQL):
        guard_sql(sql)


def test_unsafe_sql_is_a_value_error():
    assert issubclass(UnsafeSQL, ValueError)


# ---------- guard_sql: the forced LIMIT ----------

def test_a_missing_limit_is_added():
    assert guard_sql("SELECT * FROM tasks") == "SELECT * FROM tasks LIMIT 50"
    assert guard_sql("SELECT * FROM tasks;", default_limit=10) == "SELECT * FROM tasks LIMIT 10"
    assert guard_sql("SELECT * FROM tasks ORDER BY id OFFSET 20") == "SELECT * FROM tasks ORDER BY id OFFSET 20 LIMIT 50"


def test_a_small_limit_is_kept_and_a_large_one_is_clamped():
    assert guard_sql("SELECT * FROM tasks LIMIT 5") == "SELECT * FROM tasks LIMIT 5"
    assert guard_sql("select * from tasks limit 100000") == "select * from tasks limit 200"
    assert guard_sql("SELECT * FROM tasks LIMIT 100000", max_limit=30) == "SELECT * FROM tasks LIMIT 30"
    assert guard_sql("SELECT * FROM tasks LIMIT ALL") == "SELECT * FROM tasks LIMIT 200"
    assert guard_sql("SELECT * FROM tasks LIMIT 500 OFFSET 10") == "SELECT * FROM tasks LIMIT 200 OFFSET 10"


def test_only_the_outer_limit_counts():
    sql = "SELECT * FROM (SELECT * FROM tasks LIMIT 5) s"
    assert guard_sql(sql) == sql + " LIMIT 50"
    out = guard_sql("WITH r AS (SELECT * FROM notes LIMIT 5) SELECT * FROM r")
    assert out.endswith("SELECT * FROM r LIMIT 50")


def test_a_limit_word_inside_a_string_is_not_a_limit():
    assert guard_sql("SELECT 'LIMIT 5' AS x") == "SELECT 'LIMIT 5' AS x LIMIT 50"
