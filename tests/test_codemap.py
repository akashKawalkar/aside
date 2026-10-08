from __future__ import annotations

import json
import re
import textwrap

import pytest

import codemap


def build(tmp_path, files: dict[str, str], *, include_isolated=False, previous=None):
    """Write a small project, analyse it, and return (map data, {label: layer})."""
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")

    project = codemap.Project(codemap.discover(tmp_path))
    project.analyse()
    data = codemap.build_map(project, title="t", include_isolated=include_isolated, previous=previous)
    return data


def edges(data) -> set[tuple[str, str]]:
    """(used, user) pairs by label."""
    label = {n["id"]: n["label"] for n in data["nodes"]}
    return {(label[e["sourceId"]], label[e["targetId"]]) for e in data["edges"]}


def labels(data) -> set[str]:
    return {n["label"] for n in data["nodes"]}


# ---------- what counts (your rules) ----------

def test_local_helpers_count_even_when_private(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def _row_to_dict(row):
            return dict(row)

        def create_task(row):
            return _row_to_dict(row)
    """})
    assert edges(data) == {("_row_to_dict", "create_task")}


def test_generic_and_third_party_calls_are_ignored(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        import logging
        from datetime import datetime, timedelta

        logger = logging.getLogger(__name__)

        def work(pool, cur):
            logger.info("hi")
            when = datetime.now() + timedelta(days=1)
            when.astimezone().total_seconds()
            abs(-1)
            with pool.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("select 1")
                    cursor.fetchone()
            return when
    """})
    assert data["nodes"] == [] and data["edges"] == []


def test_functions_imported_from_other_packages_are_included(tmp_path):
    data = build(tmp_path, {
        "capture/router.py": "def route(text):\n    return text\n",
        "knowledge/notes.py": "def capture_note(text):\n    return text\n",
        "capture/server.py": """
            from capture.router import route
            from knowledge.notes import capture_note

            def input_route(text):
                capture_note(route(text))
        """,
    })
    assert edges(data) == {("route", "input_route"), ("capture_note", "input_route")}


def test_calls_through_a_package_re_export_find_the_real_function(tmp_path):
    data = build(tmp_path, {
        "storage/__init__.py": "from storage.repo.tasks import create_task\n",
        "storage/repo/__init__.py": "",
        "storage/repo/tasks.py": "def create_task():\n    return 1\n",
        "capture/server.py": """
            from storage import create_task

            def make():
                return create_task()
        """,
    })
    assert edges(data) == {("create_task", "make")}
    assert {n["label"]: n["layerId"] for n in data["nodes"]}["create_task"] != \
           {n["label"]: n["layerId"] for n in data["nodes"]}["make"]  # it lives in the storage layer


def test_methods_use_the_class_qualified_name(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        class ABC:
            def franky(self):
                return 1

            def other(self):
                return self.franky()

        def outside():
            return ABC().franky()
    """})
    assert ("ABC.franky", "ABC.other") in edges(data)
    assert ("ABC.franky", "outside") in edges(data)
    assert "franky" not in labels(data)  # never the bare name


def test_module_functions_called_through_a_module_alias(tmp_path):
    data = build(tmp_path, {
        "storage/tasks.py": "def create_task():\n    return 1\n",
        "capture/a.py": """
            import storage.tasks as t

            def go():
                return t.create_task()
        """,
    })
    assert edges(data) == {("create_task", "go")}


def test_creating_a_behavioural_class_counts_but_a_data_class_does_not(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        from dataclasses import dataclass
        from pydantic import BaseModel

        class SpoolWriter:
            def close(self):
                return 1

        @dataclass
        class Result:
            n: int

        class Payload(BaseModel):
            x: int

        def run():
            writer = SpoolWriter()
            writer.close()
            return Result(1), Payload(x=1)
    """})
    assert edges(data) == {("SpoolWriter", "run"), ("SpoolWriter.close", "run")}


def test_a_function_handed_to_another_call_counts(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        import asyncio

        def parse_spool_file(path):
            return path

        async def ingest(path):
            return await asyncio.to_thread(parse_spool_file, path)
    """})
    assert edges(data) == {("parse_spool_file", "ingest")}


def test_a_lambda_that_calls_a_function_counts_for_its_enclosing_function(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def save_note(pool, payload):
            return payload

        def capture_note(text, save):
            return save(text)

        def route(pool, text):
            return capture_note(text, save=lambda payload: save_note(pool, payload))
    """})
    assert ("save_note", "route") in edges(data)
    assert ("capture_note", "route") in edges(data)


def test_default_arguments_and_registries_count(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def fetch_events():
            return []

        def run(fetch=fetch_events):
            return fetch()

        class Registry:
            def __init__(self):
                self.tools = {"create": self.create}

            def create(self):
                return 1
    """})
    assert ("fetch_events", "run") in edges(data)
    assert ("Registry.create", "Registry") in edges(data)  # the constructor is the class


def test_nested_functions_are_their_own_nodes_named_outer_dot_inner(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def helper():
            return 1

        def create_app():
            def lifespan():
                return helper()
            return lifespan
    """})
    assert ("helper", "create_app.lifespan") in edges(data)
    # returning it is a use too, but merely containing a nested def is not
    assert edges(data) == {("helper", "create_app.lifespan"), ("create_app.lifespan", "create_app")}


def test_recursion_and_repeated_calls_make_one_edge_each(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def leaf():
            return 1

        def walk(n):
            leaf(); leaf(); leaf()
            return walk(n - 1)
    """})
    assert edges(data) == {("leaf", "walk")}
    assert len(data["edges"]) == 1


# ---------- type tracking ----------

def test_typed_parameters_and_attributes_resolve_method_calls(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        class Store:
            def save(self):
                return 1

        class Service:
            def __init__(self, store: Store):
                self.store = store

            def run(self):
                return self.store.save()

        def direct(store: Store | None):
            return store.save()
    """})
    assert ("Store.save", "Service.run") in edges(data)
    assert ("Store.save", "direct") in edges(data)


def test_parameter_types_come_from_the_callers_when_they_all_agree(tmp_path):
    files = {"capture/a.py": """
        class Probe:
            def read(self):
                return 1

        def sampler(probe):
            return probe.read()

        def run_collector():
            return sampler(Probe())
    """}
    assert ("Probe.read", "sampler") in edges(build(tmp_path, files))


def test_conflicting_callers_leave_the_parameter_unknown(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        class A:
            def read(self): return 1

        class B:
            def read(self): return 2

        def sampler(probe):
            return probe.read()

        def one():
            return sampler(A())

        def two():
            return sampler(B())
    """})
    assert not any(user == "sampler" and used.endswith(".read") for used, user in edges(data))


def test_methods_are_found_on_base_classes(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        class Base:
            def shared(self):
                return 1

        class Child(Base):
            def go(self):
                return self.shared()
    """})
    assert ("Base.shared", "Child.go") in edges(data)


def test_calls_on_unknown_receivers_are_not_guessed(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        class Repo:
            def create(self):
                return 1

        def use(repo):
            return repo.create()  # nothing says what repo is
    """})
    assert edges(data) == set()


# ---------- labels, layers, scope ----------

def test_duplicate_names_get_just_enough_module_to_tell_them_apart(tmp_path):
    data = build(tmp_path, {
        "storage/repo/notes.py": "def save_note():\n    return 1\n",
        "knowledge/notes.py": "def save_note():\n    return 2\n",
        "capture/a.py": """
            from storage.repo.notes import save_note as s1
            from knowledge.notes import save_note as s2

            def go():
                return s1(), s2()
        """,
    })
    assert labels(data) == {"repo.notes.save_note", "knowledge.notes.save_note", "go"}


def test_unique_names_stay_short(tmp_path):
    data = build(tmp_path, {
        "storage/repo/tasks.py": "def create_task():\n    return 1\n",
        "capture/a.py": "from storage.repo.tasks import create_task\n\ndef go():\n    return create_task()\n",
    })
    assert labels(data) == {"create_task", "go"}


def test_layers_follow_the_folders_and_your_names(tmp_path):
    data = build(tmp_path, {
        "storage/a.py": "def low():\n    return 1\n",
        "corrections/a.py": "from storage.a import low\n\ndef c():\n    return low()\n",
        "review/a.py": "from storage.a import low\n\ndef r():\n    return low()\n",
        "capture/a.py": "from storage.a import low\n\ndef top():\n    return low()\n",
        "tool.py": "from storage.a import low\n\ndef script():\n    return low()\n",
    })
    names = [l["name"] for l in data["layers"]]
    assert names == ["storage", "review and correction", "Capture", "scripts"]  # corrections + review share one
    z = [l["zPosition"] for l in data["layers"]]
    assert z == sorted(z) and len(set(z)) == len(z)
    in_layer = {n["label"]: next(l["name"] for l in data["layers"] if l["id"] == n["layerId"]) for n in data["nodes"]}
    assert in_layer["c"] == in_layer["r"] == "review and correction"


def test_functions_without_a_relationship_are_left_out_unless_asked(tmp_path):
    files = {"capture/a.py": """
        def lonely():
            return 1

        def helper():
            return 1

        def user():
            return helper()
    """}
    assert labels(build(tmp_path, files)) == {"helper", "user"}
    assert labels(build(tmp_path, files, include_isolated=True)) == {"helper", "user", "lonely"}


def test_tests_and_virtualenvs_are_not_mapped(tmp_path):
    data = build(tmp_path, {
        "capture/a.py": "def a():\n    return 1\n\ndef b():\n    return a()\n",
        "tests/test_a.py": "from capture.a import a\n\ndef test_a():\n    a()\n",
        ".venv/lib/x.py": "def junk():\n    return 1\n\ndef more():\n    return junk()\n",
    })
    assert labels(data) == {"a", "b"}


def test_docstring_becomes_the_memo(tmp_path):
    data = build(tmp_path, {"capture/a.py": '''
        def helper():
            """Does the one thing.

            Longer detail that should not be in the memo."""
            return 1

        def user():
            return helper()
    '''})
    memos = {n["label"]: n["memo"] for n in data["nodes"]}
    assert memos["helper"] == "Does the one thing."
    assert memos["user"] == ""


# ---------- the file itself ----------

NODE_KEYS = ["id", "layerId", "x", "y", "z", "label", "memo", "iconUrl", "iconType", "style"]
LAYER_KEYS = ["id", "name", "zPosition", "opacity", "color", "visible", "width", "height", "showWalls", "wallHeight"]
EDGE_KEYS = ["id", "sourceId", "targetId", "label", "memo", "style"]
TOP_KEYS = ["id", "title", "createdAt", "updatedAt", "layers", "nodes", "edges", "objects"]


def test_the_structure_matches_the_viewers_format_exactly(tmp_path):
    data = build(tmp_path, {"capture/a.py": "def a():\n    return 1\n\ndef b():\n    return a()\n"})

    assert list(data) == TOP_KEYS
    assert data["objects"] == []
    assert all(list(l) == LAYER_KEYS for l in data["layers"])
    assert all(list(n) == NODE_KEYS and list(n["style"]) == ["color", "size", "shape"] for n in data["nodes"])
    assert all(list(e) == EDGE_KEYS and list(e["style"]) == ["direction", "lineStyle", "thickness", "color"]
               for e in data["edges"])
    assert all(n["iconUrl"] is None and n["iconType"] is None for n in data["nodes"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", data["createdAt"])


def test_every_reference_in_the_file_points_at_something_that_exists(tmp_path):
    data = build(tmp_path, {
        "storage/a.py": "def low():\n    return 1\n",
        "capture/a.py": "from storage.a import low\n\ndef top():\n    return low()\n",
    })
    layer_ids = {l["id"] for l in data["layers"]}
    node_ids = {n["id"] for n in data["nodes"]}
    assert all(n["layerId"] in layer_ids for n in data["nodes"])
    assert all(e["sourceId"] in node_ids and e["targetId"] in node_ids for e in data["edges"])
    assert len(node_ids) == len(data["nodes"])
    assert all(re.fullmatch(r"[0-9a-f-]{36}", x) for x in [data["id"], *layer_ids, *node_ids])


def test_edges_point_from_the_function_used_to_the_function_using_it(tmp_path):
    data = build(tmp_path, {"capture/a.py": "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"})
    by_id = {n["id"]: n["label"] for n in data["nodes"]}
    [edge] = data["edges"]
    assert (by_id[edge["sourceId"]], by_id[edge["targetId"]]) == ("helper", "caller")  # as on the hand-drawn map


def test_layout_keeps_nodes_apart_and_inside_their_layer(tmp_path):
    body = "\n".join(f"def f{i}():\n    return 1\n" for i in range(30))
    body += "\ndef top():\n    return " + " + ".join(f"f{i}()" for i in range(30)) + "\n"
    data = build(tmp_path, {"capture/a.py": body})

    layer = data["layers"][0]
    points = [(n["x"], n["y"]) for n in data["nodes"]]
    assert all(abs(x) <= layer["width"] / 2 and abs(y) <= layer["height"] / 2 for x, y in points)
    gaps = [((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for i, a in enumerate(points) for b in points[i + 1:]]
    assert min(gaps) >= 0.95
    assert all(n["z"] == layer["zPosition"] for n in data["nodes"])


# ---------- regenerating ----------

SOURCE = {"capture/a.py": "def helper():\n    return 1\n\ndef user():\n    return helper()\n"}


def test_ids_are_the_same_every_run(tmp_path):
    first = build(tmp_path, SOURCE)
    second = build(tmp_path, SOURCE)
    assert [n["id"] for n in first["nodes"]] == [n["id"] for n in second["nodes"]]
    assert [e["id"] for e in first["edges"]] == [e["id"] for e in second["edges"]]
    assert first["layers"][0]["id"] == second["layers"][0]["id"]


def test_regenerating_keeps_hand_moved_positions_and_edited_memos(tmp_path):
    first = build(tmp_path, SOURCE)
    helper = next(n for n in first["nodes"] if n["label"] == "helper")
    helper["x"], helper["y"], helper["memo"] = 7.5, -3.25, "my own words"

    second = build(tmp_path, {**SOURCE, "capture/b.py": "from capture.a import user\n\ndef extra():\n    return user()\n"},
                   previous=first)

    kept = next(n for n in second["nodes"] if n["label"] == "helper")
    assert (kept["x"], kept["y"], kept["memo"]) == (7.5, -3.25, "my own words")
    assert "extra" in labels(second)  # new code appears
    assert second["createdAt"] == first["createdAt"]


def test_the_command_line_writes_a_file_and_rewrites_it_in_place(tmp_path, capsys):
    (tmp_path / "capture").mkdir()
    (tmp_path / "capture" / "a.py").write_text("def a():\n    return 1\n\ndef b():\n    return a()\n")
    out = tmp_path / "map.json"

    assert codemap.main(["--root", str(tmp_path), "--out", str(out), "--report"]) == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert out.read_text(encoding="utf-8").startswith('{\n  "id"')
    assert len(first["nodes"]) == 2

    assert codemap.main(["--root", str(tmp_path), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["nodes"] == first["nodes"]  # nothing moved


def test_comparing_with_a_hand_drawn_map_reports_matches(tmp_path, capsys):
    data = build(tmp_path, {"capture/a.py": "def helper():\n    return 1\n\ndef user():\n    return helper()\n"})
    hand = {
        "layers": [{"id": "L", "name": "Capture"}],
        "nodes": [
            {"id": "n1", "layerId": "L", "label": "helper"},
            {"id": "n2", "layerId": "L", "label": "user"},
            {"id": "n3", "layerId": "L", "label": "gone_function"},
        ],
        "edges": [{"sourceId": "n1", "targetId": "n2"}],
    }
    codemap.compare(data, hand)
    out = capsys.readouterr().out
    assert "2 of 3" in out and "generated also has 1 of them" in out and "gone_function" in out


def test_syntax_errors_are_skipped_not_fatal(tmp_path, capsys):
    data = build(tmp_path, {
        "capture/good.py": "def a():\n    return 1\n\ndef b():\n    return a()\n",
        "capture/bad.py": "def broken(:\n",
    })
    assert labels(data) == {"a", "b"}
    assert "skipped" in capsys.readouterr().err


def test_a_local_alias_for_a_function_is_followed(tmp_path):
    data = build(tmp_path, {"capture/a.py": """
        def default_handler():
            return 1

        def create_app(handler=None):
            handler_fn = handler or default_handler

            def route():
                return handler_fn()

            return route
    """})
    assert ("default_handler", "create_app.route") in edges(data)
    assert ("default_handler", "create_app") in edges(data)


def test_the_tooling_scripts_are_not_mapped(tmp_path):
    data = build(tmp_path, {
        "capture/a.py": "def a():\n    return 1\n\ndef b():\n    return a()\n",
        "llmmap.py": "def x():\n    return 1\n\ndef y():\n    return x()\n",
    })
    assert labels(data) == {"a", "b"}
