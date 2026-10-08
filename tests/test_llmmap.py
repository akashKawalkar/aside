from __future__ import annotations

import ast
import json
import re
import textwrap

import pytest

import codemap
import llmmap


def generate(tmp_path, files: dict[str, str], **options):
    """Write a small project and return (model, text)."""
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")

    project = codemap.Project(codemap.discover(tmp_path))
    project.analyse()
    model = llmmap.build_model(
        project, tmp_path,
        fold_helpers=options.get("fold_helpers", True), max_doc=options.get("max_doc", 110),
    )
    return model, llmmap.render_text(model, title="t", lines=options.get("lines", True))


def items(model) -> dict[str, llmmap.Item]:
    return {i.label: i for layer in model["layers"] for f in layer["files"] for i in f["items"]}


def fn(source: str):
    return ast.parse(textwrap.dedent(source)).body[0]


# ---------- the small renderers ----------

@pytest.mark.parametrize("source, expected", [
    ("def f(a, b): pass", "(a, b)"),
    ("def f(self, a: int): pass", "(a:int)"),
    ("def f(cls, a): pass", "(a)"),
    ("def f(a, b: str | None = None, c=3): pass", "(a, b:str | None=None, c=3)"),
    ("def f(a, *, b: int = 1): pass", "(a, *, b:int=1)"),
    ("def f(*args, **kwargs): pass", "(*args, **kwargs)"),
    ("def f(a) -> dict[str, int]: pass", "(a)->dict[str, int]"),
    ("async def f(a: 'Event'): pass", "(a:Event)"),                     # forward references lose their quotes
    ("def f(a=some_call(1, 2)): pass", "(a=…)"),                        # a computed default is not worth showing
    ("def f(a='x' * 3, b='a very long default string'): pass", "(a=…, b=…)"),
])
def test_signatures(source, expected):
    assert llmmap.signature(fn(source)) == expected


def test_a_very_long_signature_is_cut():
    params = ", ".join(f"parameter_number_{i}: int" for i in range(30))
    sig = llmmap.signature(fn(f"def f({params}): pass"))
    assert len(sig) <= llmmap.MAX_SIGNATURE and sig.endswith("…")


@pytest.mark.parametrize("doc, expected", [
    ("Creates a task. It is due in 24 hours.", "Creates a task."),
    ("One line without a full stop", "One line without a full stop"),
    ("First paragraph\nspans two lines.\n\nSecond paragraph.", "First paragraph spans two lines."),
    ("", ""),
])
def test_first_sentence(doc, expected):
    assert llmmap.first_sentence(doc, 200) == expected


def test_first_sentence_is_cut_to_the_limit():
    out = llmmap.first_sentence("word " * 100, 40)
    assert len(out) <= 40 and out.endswith("…")


def test_sql_tables_split_into_read_and_written():
    reads, writes = llmmap.sql_tables([
        "SELECT a FROM tasks JOIN task_log ON x WHERE id = %s",
        "INSERT INTO notes (a) VALUES (%s)",
        "UPDATE schedule SET title = %s",
        "DELETE FROM tasks WHERE id = %s",
        "just some text about going FROM here",       # not SQL: no statement keyword
    ])
    assert reads == ["task_log", "tasks"]
    assert writes == ["notes", "schedule", "tasks"]


def test_delete_from_is_a_write_not_also_a_read():
    reads, writes = llmmap.sql_tables(["DELETE FROM tasks WHERE id = %s"])
    assert (reads, writes) == ([], ["tasks"])


# ---------- facts about each definition ----------

def test_where_things_live_and_how_they_are_called(tmp_path):
    model, text = generate(tmp_path, {"storage/tasks.py": '''
        async def create_task(pool, *, text: str, due_at: str | None = None) -> dict:
            """Create a task. Due in 24 hours by default."""
            return {}
    ''', "capture/a.py": "from storage.tasks import create_task\n\nasync def go(pool):\n    return await create_task(pool, text='x')\n"})

    item = items(model)["create_task"]
    assert (item.layer, item.path, item.line) == ("storage", "storage/tasks.py", 2)
    assert item.sig == "(pool, *, text:str, due_at:str | None=None)->dict"
    assert item.doc == "Create a task."
    assert "async" in item.tags
    assert "create_task(pool, *, text:str, due_at:str | None=None)->dict L2 [async]" in text
    assert "# Create a task." in text


def test_uses_and_used_by_are_two_views_of_the_same_edge(tmp_path):
    model, text = generate(tmp_path, {"capture/a.py": """
        def low(): return 1
        def mid(): return low()
        def high(): return mid() + low()
    """})
    got = items(model)
    assert got["high"].uses == {"mid", "low"} and got["mid"].by == {"high"}
    assert got["low"].by == {"mid", "high"}
    for item in got.values():
        for used in item.uses:
            assert item.label in got[used].by
        for caller in item.by:
            assert item.label in got[caller].uses


def test_classes_show_bases_constructor_and_data_fields(tmp_path):
    model, text = generate(tmp_path, {"capture/a.py": '''
        from dataclasses import dataclass

        class Base:
            def go(self): return 1

        class Worker(Base):
            """Does the work."""
            def __init__(self, name: str, retries: int = 3):
                self.name = name

        @dataclass
        class Result:
            ok: bool
            note: str

        def run():
            Worker("x").go()
            return Result(True, "")
    '''})
    got = items(model)
    assert got["Worker"].bases == ["Base"] and got["Worker"].sig == "(name:str, retries:int=3)"
    assert got["Result"].fields == ["ok:bool", "note:str"] and "dataclass" in got["Result"].tags
    assert "class Worker(Base)(name:str, retries:int=3)" in text
    assert "fields: ok:bool, note:str" in text


def test_long_field_lists_are_cut(tmp_path):
    lines = [f"    f{i}: int" for i in range(20)]
    source = "\n".join(["from dataclasses import dataclass", "", "@dataclass", "class Big:", *lines]) + "\n"
    model, text = generate(tmp_path, {"capture/a.py": source})
    shown = items(model)["Big"].fields
    assert len(shown) == llmmap.MAX_FIELDS + 1 and shown[-1] == "+6 more"   # every definition is listed, connected or not


def test_database_tables_come_from_inline_sql_and_module_constants(tmp_path):
    model, text = generate(tmp_path, {"storage/repo.py": '''
        INSERT_SQL = "INSERT INTO tasks (text) VALUES (%s)"
        LIST_SQL = """
            SELECT id FROM tasks
            JOIN task_log ON task_log.task_id = tasks.id
        """

        def create(cur):
            cur.execute(INSERT_SQL, ("x",))

        def listing(cur):
            """Not a query: mentions FROM and INTO in prose."""
            cur.execute(LIST_SQL)
            cur.execute("DELETE FROM task_log WHERE id = 1")
    '''})
    got = items(model)
    assert (got["create"].reads, got["create"].writes) == ([], ["tasks"])
    assert (got["listing"].reads, got["listing"].writes) == (["task_log", "tasks"], ["task_log"])
    assert "db w:tasks" in text and "db r:task_log,tasks w:task_log" in text


# ---------- entry points and the wider picture ----------

def test_http_routes_are_listed_and_marked(tmp_path):
    model, text = generate(tmp_path, {"capture/server.py": """
        def create_app():
            app = object()

            @app.post("/input")
            async def input_route(request):
                return 1

            @app.get("/health")
            async def health():
                return 2

            return app
    """})
    assert ("POST /input", "create_app.input_route") in model["entries"]
    assert ("GET /health", "create_app.health") in model["entries"]
    assert "POST /input  ->  create_app.input_route" in text
    assert "input_route(request) L6 [async]  POST /input" in text
    assert "no-callers" not in items(model)["create_app.input_route"].tags  # a route is an entry, not dead code


def test_command_line_commands_and_script_entry_points(tmp_path):
    model, text = generate(tmp_path, {
        "capture/__main__.py": """
            import argparse

            def run():
                return 1

            def main():
                sub = argparse.ArgumentParser().add_subparsers()
                sub.add_parser("collect")
                sub.add_parser("ingest")
                return run()

            if __name__ == "__main__":
                main()
        """,
        "tool.py": """
            def work(): return 1

            def main(): return work()

            if __name__ == "__main__":
                main()
        """,
    })
    what = {w for w, _ in model["entries"]}
    assert "python -m capture {collect|ingest}" in what
    assert "python tool.py" in what


def test_environment_variables_and_third_party_libraries(tmp_path):
    model, text = generate(tmp_path, {"capture/a.py": """
        import os
        import json
        import psycopg
        from pydantic import BaseModel
        from capture import b

        URL = os.environ["DATABASE_URL"]
        PORT = int(os.environ.get("AGENT_PORT", 8787))
        KEY = os.getenv("API_KEY")

        def go():
            return b.helper()
    """, "capture/b.py": "def helper():\n    return 1\n"})
    assert model["env"] == ["AGENT_PORT", "API_KEY", "DATABASE_URL"]
    assert model["libs"] == ["psycopg", "pydantic"]          # not json (standard library) or capture (ours)
    assert "ENVIRONMENT: AGENT_PORT, API_KEY, DATABASE_URL" in text
    assert "--- capture/a.py  [psycopg, pydantic]" in text


def test_calls_between_layers_are_counted(tmp_path):
    model, text = generate(tmp_path, {
        "storage/a.py": "def one(): return 1\n\ndef two(): return 2\n",
        "capture/b.py": "from storage.a import one, two\n\ndef x(): return one()\n\ndef y(): return one() + two()\n",
    })
    assert model["flow"][("Capture", "storage")] == 3
    assert "Capture -> storage 3" in text


def test_layers_are_listed_top_down(tmp_path):
    model, _ = generate(tmp_path, {
        "storage/a.py": "def one(): return 1\n",
        "capture/b.py": "from storage.a import one\n\ndef x(): return one()\n",
        "agent/c.py": "from storage.a import one\n\ndef z(): return one()\n",
    })
    assert [l["name"] for l in model["layers"]] == ["Capture", "agent", "storage"]


# ---------- what is flagged ----------

def test_things_nothing_calls_are_flagged_but_entries_and_dunders_are_not(tmp_path):
    model, _ = generate(tmp_path, {"capture/a.py": """
        def used(): return 1
        def orphan(): return 1
        def started(): return 1
        def go(): return used()

        class Thing:
            def __repr__(self): return "t"
            @property
            def size(self): return 1
            def helper(self): return 1

        app = started()
    """})
    got = items(model)
    flagged = {label for label, i in got.items() if "no-callers" in i.tags}
    assert "orphan" in flagged and "go" in flagged and "Thing.helper" in flagged
    assert "used" not in flagged
    assert "started" not in flagged and "<module>" in got["started"].by      # used when the module loads
    assert "Thing.__repr__" not in flagged and "Thing.size" not in flagged


def test_interface_methods_are_not_flagged(tmp_path):
    model, _ = generate(tmp_path, {"capture/a.py": """
        from typing import Protocol

        class Repo(Protocol):
            def create(self): ...

        def go(repo: Repo):
            return repo.create()
    """})
    assert "no-callers" not in items(model)["Repo.create"].tags


# ---------- folding small helpers ----------

HELPERS = {"capture/a.py": '''
    def save(x): return x

    def _clean(x): return save(x)

    def _prepare(x):
        """Prepare it."""
        return _clean(x)

    def process(x): return _prepare(x)
    def other(x): return _prepare(x) + 1
'''}


def test_a_private_helper_with_one_caller_is_folded_into_it(tmp_path):
    model, text = generate(tmp_path, {"capture/a.py": """
        def save(x): return x

        def _clean(x): return save(x)

        def process(x): return _clean(x)
    """})
    got = items(model)
    assert "_clean" not in got                          # no entry of its own...
    assert got["process"].helpers[0][0] == "_clean"     # ...it is recorded on its caller
    assert got["process"].uses == {"save"}              # and what it used is credited to the caller
    assert got["save"].by == {"process"}                # ...so nothing points at a name that is not listed
    assert "~ _clean@" in text
    assert model["folded"] == 1


def test_a_helper_shared_by_two_callers_stays_listed(tmp_path):
    model, _ = generate(tmp_path, HELPERS)
    got = items(model)
    assert "_prepare" in got and got["_prepare"].by == {"process", "other"}
    # _clean has one caller (_prepare), so it is folded into it; _prepare stays because two functions use it
    assert "_clean" not in got
    assert [h[0] for h in got["_prepare"].helpers] == ["_clean"]
    assert got["_prepare"].uses == {"save"} and got["save"].by == {"_prepare"}


def test_folding_follows_chains(tmp_path):
    model, _ = generate(tmp_path, {"capture/a.py": """
        def save(x): return x
        def _a(x): return save(x)
        def _b(x): return _a(x)
        def top(x): return _b(x)
    """})
    got = items(model)
    assert set(got) == {"save", "top"}
    assert got["top"].uses == {"save"} and {h[0] for h in got["top"].helpers} == {"_a", "_b"}


def test_helpers_are_not_folded_across_files_or_when_asked_not_to(tmp_path):
    files = {
        "capture/a.py": "from capture.b import _shared\n\ndef top(): return _shared()\n",
        "capture/b.py": "def _shared(): return 1\n",
    }
    assert "_shared" in items(generate(tmp_path, files)[0])

    model, _ = generate(tmp_path / "x", {"capture/a.py": "def _h(): return 1\n\ndef top(): return _h()\n"}, fold_helpers=False)
    assert {"_h", "top"} <= set(items(model))


def test_helpers_that_touch_the_database_or_are_decorated_are_kept(tmp_path):
    model, _ = generate(tmp_path, {"storage/a.py": '''
        def _insert(cur):
            cur.execute("INSERT INTO tasks (a) VALUES (1)")

        def create(cur):
            return _insert(cur)
    '''})
    assert "_insert" in items(model)


# ---------- the files ----------

def test_no_lines_removes_line_numbers_only(tmp_path):
    files = {"capture/a.py": "def a(): return 1\n\ndef b(): return a()\n"}
    with_lines = generate(tmp_path, files)[1]
    without = generate(tmp_path, files, lines=False)[1]
    assert re.search(r"\bL\d+\b", with_lines) and not re.search(r"\bL\d+\b", without)
    assert "> a" in without


def test_json_carries_the_same_facts_as_the_text(tmp_path):
    files = {
        "storage/a.py": "INS = 'INSERT INTO tasks (a) VALUES (1)'\n\ndef save(cur):\n    '''Save it.'''\n    cur.execute(INS)\n",
        "capture/b.py": "from storage.a import save\n\nasync def go(cur):\n    return save(cur)\n",
    }
    model, text = generate(tmp_path, files)
    data = json.loads(llmmap.render_json(model, title="t", lines=True))

    names = {i["name"] for l in data["layers"] for f in l["files"] for i in f["items"]}
    assert names == set(items(model))
    save = next(i for l in data["layers"] for f in l["files"] for i in f["items"] if i["name"] == "save")
    assert save["by"] == ["go"] and save["writes"] == ["tasks"] and save["doc"] == "Save it." and save["line"] == 3
    assert "tags" not in save and "reads" not in save          # empty fields are dropped, not written as null
    assert ": " not in llmmap.render_json(model, title="t", lines=True)[:80]     # compact separators


def test_the_map_is_much_smaller_than_the_viewer_file(tmp_path):
    body = "\n".join(f"def f{i}():\n    return {'f%d()' % (i - 1) if i else 1}\n" for i in range(60))
    files = {"capture/a.py": body}
    model, text = generate(tmp_path, files)

    project = codemap.Project(codemap.discover(tmp_path))
    project.analyse()
    viewer = json.dumps(codemap.build_map(project, title="t", include_isolated=False, previous=None), indent=2)
    assert len(text) * 3 < len(viewer)


def test_command_line_writes_text_or_json_and_reports_the_size(tmp_path, capsys):
    (tmp_path / "capture").mkdir()
    (tmp_path / "capture" / "a.py").write_text("def a(): return 1\n\ndef b(): return a()\n", encoding="utf-8")

    out = tmp_path / "map.txt"
    assert llmmap.main(["--root", str(tmp_path), "--out", str(out), "--title", "Demo"]) == 0
    assert out.read_text(encoding="utf-8").startswith("CODE MAP: Demo")
    assert "tokens" in capsys.readouterr().out

    out_json = tmp_path / "map.json"
    assert llmmap.main(["--root", str(tmp_path), "--out", str(out_json), "--format", "json"]) == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["title"] == "Personal Agent"


def test_tooling_scripts_are_not_part_of_the_map(tmp_path):
    model, _ = generate(tmp_path, {
        "capture/a.py": "def a(): return 1\n\ndef b(): return a()\n",
        "codemap.py": "def a2(): return 1\n\ndef b2(): return a2()\n",
        "llmmap.py": "def a3(): return 1\n\ndef b3(): return a3()\n",
    })
    assert set(items(model)) == {"a", "b"}
