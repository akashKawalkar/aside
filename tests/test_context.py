from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from context.compile import compile_context
from context.items import Item, Situation
from context.packer import pack, render
from context.recipe import Recipe, SourceSpec, load_recipe, load_recipes
from context.selector import LLMSelector, OffSelector, RulesSelector, make_selector
from context.sources import EmptySource
from context.sources.current_session import CurrentSessionSource
from context.sources.schedule import ScheduleSource
from context.sources.tasks import TasksSource
from llm.client import ModelProfile

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
PROFILE = ModelProfile(name="t", provider="fake", window=1000, chars_per_token=1.0, safety_margin=1.0)  # 1 char = 1 token


def item(id, source, size=10, **kw):
    return Item(id=id, text="x" * size, source=source, **kw)


def recipe(*specs, fraction=1.0, selector="off"):
    return Recipe("t", fraction, selector, tuple(specs))


def ids(result):
    return [i.id for i in result.chosen]


def reasons(result):
    return {d.id: d.reason for d in result.dropped}


class FakeSource:
    def __init__(self, name, items=(), error=None):
        self.name, self._items, self._error = name, list(items), error

    async def fetch(self, situation):
        if self._error:
            raise self._error
        return list(self._items)


# ---------- packer ----------

def test_higher_priority_source_packs_first_when_budget_is_tight():
    r = recipe(SourceSpec("persistent_file", 90), SourceSpec("notes", 50), fraction=0.1)  # budget 100
    result = pack([item("n", "notes", 60), item("p", "persistent_file", 60)], r, PROFILE, now=NOW)
    assert ids(result) == ["p"]
    assert reasons(result) == {"n": "over_budget"}


def test_source_cap_limits_one_source_but_not_others():
    r = recipe(SourceSpec("notes", 50, cap_fraction=0.2), SourceSpec("tasks", 40))  # notes cap 200 of 1000
    items = [item("n1", "notes", 150), item("n2", "notes", 150), item("t1", "tasks", 150)]
    result = pack(items, r, PROFILE, now=NOW)
    assert ids(result) == ["n1", "t1"]
    assert reasons(result) == {"n2": "source_cap"}


def test_max_items_and_item_priority_order():
    r = recipe(SourceSpec("skills", 80, max_items=2))
    items = [item("a", "skills", priority=1), item("b", "skills", priority=5), item("c", "skills", priority=3)]
    result = pack(items, r, PROFILE, now=NOW)
    assert ids(result) == ["b", "c"]
    assert reasons(result) == {"a": "max_items"}


def test_an_item_that_does_not_fit_does_not_block_smaller_ones():
    r = recipe(SourceSpec("notes", 50), fraction=0.1)  # budget 100
    result = pack([item("big", "notes", 500), item("small", "notes", 20)], r, PROFILE, now=NOW)
    assert ids(result) == ["small"]


def test_filters_give_explicit_reasons():
    r = recipe(SourceSpec("notes", 50, min_confidence=0.5))
    items = [
        item("retired", "notes", status="retired"),
        item("shelved", "notes", status="shelved"),
        item("old", "notes", valid_to=NOW - timedelta(days=1)),
        item("later", "notes", valid_from=NOW + timedelta(days=1)),
        item("unsure", "notes", confidence=0.2),
        item("stray", "tasks"),
        item("provisional", "notes", status="provisional"),
        item("ok", "notes"),
    ]
    result = pack(items, r, PROFILE, now=NOW)
    assert ids(result) == ["provisional", "ok"]
    assert reasons(result) == {
        "retired": "filtered:retired", "shelved": "filtered:shelved", "old": "expired",
        "later": "not_yet_valid", "unsure": "low_confidence", "stray": "not_in_recipe",
    }


def test_by_source_totals_and_render_order():
    r = recipe(SourceSpec("notes", 50), SourceSpec("persistent_file", 90))
    items = [Item("n", "a note", "notes"), Item("p", "a fact", "persistent_file")]
    result = pack(items, r, PROFILE, now=NOW)
    assert result.by_source == {"persistent_file": 6, "notes": 6}
    assert result.tokens == 12
    text = render(result, r)
    assert text.index("persistent file") < text.index("notes")
    assert "- a fact" in text and "- a note" in text


def test_packing_is_deterministic():
    r = recipe(SourceSpec("notes", 50), fraction=0.1)
    items = [item(f"n{i}", "notes", 30) for i in range(6)]
    assert ids(pack(items, r, PROFILE, now=NOW)) == ids(pack(items, r, PROFILE, now=NOW))


# ---------- recipes ----------

def test_shipped_recipes_load_and_follow_the_priority_order():
    recipes = load_recipes()
    assert {"chat", "schedule", "extraction"} <= set(recipes)
    prio = {s.name: s.priority for s in recipes["chat"].sources}
    assert prio["persistent_file"] > prio["skills"] > prio["schedule"] > prio["tasks"] > prio["notes"]
    assert recipes["chat"].spec("skills").max_items == 2
    assert recipes["chat"].spec("observations").max_items == 3


def test_recipe_hash_tracks_settings():
    a = load_recipe("chat")
    assert a.hash == load_recipe("chat").hash
    changed = Recipe(a.name, a.input_fraction + 0.1, a.selector, a.sources)
    assert changed.hash != a.hash


def test_unknown_recipe_is_an_error():
    with pytest.raises(ValueError):
        load_recipe("nope")


# ---------- selector ----------

@pytest.mark.asyncio
async def test_selectors():
    items = [item("always", "notes", core=True, situations=("chat",)), item("chat-only", "notes", situations=("chat",)),
             item("anywhere", "notes"), item("sched-only", "notes", situations=("schedule",))]
    situation = Situation("extraction")

    kept, dropped = await OffSelector().select(items, situation)
    assert len(kept) == 4 and not dropped

    kept, dropped = await RulesSelector().select(items, situation)
    assert [i.id for i in kept] == ["always", "anywhere"]
    assert {d.id: d.reason for d in dropped} == {"chat-only": "selector", "sched-only": "selector"}

    kept, _ = await RulesSelector().select(items, Situation("extraction", tags=("schedule",)))
    assert "sched-only" in [i.id for i in kept]

    assert isinstance(make_selector("rules"), RulesSelector)
    kept, _ = await LLMSelector().select(items, situation)
    assert [i.id for i in kept] == ["always", "anywhere"]
    with pytest.raises(ValueError):
        make_selector("magic")


# ---------- sources ----------

IST = timezone(timedelta(hours=5, minutes=30))


async def test_schedule_source_formats_in_ist():
    seen = {}

    async def fetch_range(*, start, end):
        seen.update(start=start, end=end)
        return [{"id": 7, "title": "Standup", "start_at": datetime(2026, 10, 6, 3, 30, tzinfo=timezone.utc),
                 "end_at": datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)}]

    src = ScheduleSource(fetch_range, clock=lambda: datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc))
    [it] = await src.fetch(Situation("chat"))
    assert it.id == "schedule:7" and "09:00-09:30 Standup" in it.text and it.provenance == "schedule:7"
    assert seen["end"] - seen["start"] == timedelta(days=2) and seen["start"].utcoffset() == timedelta(hours=5, minutes=30)


async def test_tasks_source_orders_by_due_and_keeps_undated_last():
    async def fetch_tasks(*, status):
        assert status == "pending"
        now = datetime.now(timezone.utc)
        return [{"id": 1, "text": "later", "due_at": now + timedelta(days=2)},
                {"id": 2, "text": "undated", "due_at": None},
                {"id": 3, "text": "soon", "due_at": now + timedelta(hours=1)}]

    items = await TasksSource(fetch_tasks).fetch(Situation("chat"))
    assert [i.id for i in sorted(items, key=lambda i: -i.priority)] == ["task:3", "task:1", "task:2"]
    by_id = {i.id: i.text for i in items}
    assert "(due " in by_id["task:3"] and "(due" not in by_id["task:2"]


async def test_tasks_source_leaves_out_long_overdue_tasks():
    async def fetch_tasks(*, status):
        now = datetime.now(timezone.utc)
        return [{"id": 1, "text": "stale", "due_at": now - timedelta(days=5)},
                {"id": 2, "text": "just missed", "due_at": now - timedelta(hours=5)},
                {"id": 3, "text": "undated", "due_at": None}]

    assert sorted(i.id for i in await TasksSource(fetch_tasks).fetch(Situation("chat"))) == ["task:2", "task:3"]


async def test_current_session_source_skips_the_query_when_it_is_the_user_message():
    items = await CurrentSessionSource().fetch(Situation("chat", query="what now?", extra={"is_user_message": True}))
    assert items == []      # /input already sends the query as the user message; repeating it wastes tokens


async def test_current_session_source_marks_the_request_core():
    items = await CurrentSessionSource().fetch(Situation("chat", query="what now?", extra={"session_text": ["earlier"]}))
    assert [(i.id, i.core) for i in items] == [("session:query", True), ("session:0", False)]
    assert await EmptySource("notes").fetch(Situation("chat")) == []


# ---------- compile ----------

async def test_compile_logs_offered_chosen_and_dropped():
    r = recipe(SourceSpec("persistent_file", 90), SourceSpec("notes", 50), fraction=0.1)
    sources = {"persistent_file": FakeSource("persistent_file", [item("p", "persistent_file", 60)]),
               "notes": FakeSource("notes", [item("n", "notes", 60)])}
    saved = []

    async def save_log(log):
        saved.append(log)
        return 41

    out = await compile_context(Situation("chat", query="hi"), r, sources, PROFILE, save_log=save_log, now=NOW)
    assert out.id == 41 and saved == [out.log]
    log = out.log
    assert [c["id"] for c in log["chosen"]] == ["p"] and log["tokens"] == 60
    assert [o["id"] for o in log["offered"]] == ["p", "n"]
    assert log["dropped"] == [{"id": "n", "source": "notes", "reason": "over_budget", "tokens": 60}]
    assert (log["recipe_name"], log["recipe_hash"], log["situation"], log["query"]) == ("t", r.hash, "chat", "hi")
    assert out.text.startswith("## persistent file") and "## notes" not in out.text


async def test_a_failing_source_is_dropped_not_fatal():
    r = recipe(SourceSpec("notes", 50), SourceSpec("tasks", 40))
    sources = {"notes": FakeSource("notes", error=RuntimeError("db down")), "tasks": FakeSource("tasks", [item("t", "tasks")])}
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW)
    assert ids(out.result) == ["t"]
    assert {"id": "source:notes", "source": "notes", "reason": "source_error", "tokens": 0} in out.log["dropped"]
    assert out.id is None


async def test_recipe_naming_a_missing_source_is_an_error():
    with pytest.raises(ValueError, match="notes"):
        await compile_context(Situation("chat"), recipe(SourceSpec("notes", 50)), {}, PROFILE, now=NOW)


async def test_selector_runs_before_packing():
    r = recipe(SourceSpec("notes", 50), selector="rules")
    sources = {"notes": FakeSource("notes", [item("keep", "notes"), item("skip", "notes", situations=("schedule",))])}
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW)
    assert ids(out.result) == ["keep"]
    assert {"id": "skip", "source": "notes", "reason": "selector", "tokens": 10} in out.log["dropped"]


# ---------- M4: budget ceiling, render owns formatting, gate in compile ----------

def test_budget_is_a_fraction_of_the_window_capped_by_the_ceiling():
    big = ModelProfile(name="big", provider="fake", window=1_048_576)
    assert Recipe("t", 0.5, "off", ()).budget(big.window) == 524_288                           # no ceiling: the fraction alone
    assert Recipe("t", 0.5, "off", (), max_input_tokens=8000).budget(big.window) == 8000      # ceiling binds on a 1M window
    assert Recipe("t", 0.5, "off", (), max_input_tokens=8000).budget(1000) == 500              # small window: fraction binds


def test_shipped_recipes_have_a_ceiling_so_caps_actually_bind():
    for name in ("chat", "schedule", "extraction"):
        r = load_recipe(name)
        assert r.max_input_tokens, f"{name} has no max_input_tokens"
        assert r.budget(1_048_576) == r.max_input_tokens


def test_ceiling_is_part_of_the_recipe_hash():
    a = Recipe("t", 0.5, "off", ())
    assert a.hash != Recipe("t", 0.5, "off", (), max_input_tokens=8000).hash


def test_a_ceiling_makes_a_big_window_drop_items():
    r = Recipe("t", 0.5, "off", (SourceSpec("notes", 50),), max_input_tokens=25)
    huge = ModelProfile(name="huge", provider="fake", window=1_000_000, chars_per_token=1.0, safety_margin=1.0)
    result = pack([item(f"n{i}", "notes", 10) for i in range(5)], r, huge, now=NOW)
    assert len(result.chosen) == 2 and {d.reason for d in result.dropped} == {"source_cap"}


def test_render_groups_under_subheadings_and_does_not_double_bullet():
    r = recipe(SourceSpec("persistent_file", 90), SourceSpec("notes", 50))
    items = [
        Item("p1", "Lives in Pune", "persistent_file", group="Identity"),
        Item("p2", "Never before 07:00", "persistent_file", group="Preferences"),
        Item("p3", "IST everywhere", "persistent_file", group="Identity"),
        Item("n1", "tennis moved\nto 18:30", "notes"),
    ]
    text = render(pack(items, r, PROFILE, now=NOW), r)
    assert text == (
        "## persistent file\n### Identity\n- Lives in Pune\n- IST everywhere\n### Preferences\n- Never before 07:00\n\n"
        "## notes\n- tennis moved\n  to 18:30"
    )
    assert "- ##" not in text and "- -" not in text                       # the bug this replaced


async def test_compile_gates_an_oversized_item():
    r = recipe(SourceSpec("notes", 50))
    sources = {"notes": FakeSource("notes", [Item("n1", "word " * 400, "notes")])}
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW, max_item_tokens=200)
    assert "[truncated:" in out.text and out.result.tokens <= 200


async def test_a_failing_source_is_dropped_visibly_not_silently():
    r = recipe(SourceSpec("skills", 80), SourceSpec("notes", 50))
    sources = {"skills": FakeSource("skills", error=RuntimeError("boom")), "notes": FakeSource("notes", [item("n", "notes")])}
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW)
    assert ids(out.result) == ["n"]
    assert [(d.id, d.reason) for d in out.result.dropped] == [("source:skills", "source_error")]

async def test_privacy_layer_drops_denied_tags(monkeypatch):
    from config import load_config, Privacy
    def mock_load_config():
        cfg = load_config()
        cfg.privacy = Privacy(deny_tags=["journal"])
        return cfg
    monkeypatch.setattr("context.compile.load_config", mock_load_config)
    r = recipe(SourceSpec("notes", 50))
    sources = {"notes": FakeSource("notes", [
        Item(id="keep", text="x", source="notes", tags=["work"]),
        Item(id="drop", text="x", source="notes", tags=["journal"])
    ])}
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW)
    assert ids(out.result) == ["keep"]
    assert {"id": "drop", "source": "notes", "reason": "privacy_denied_tag", "tokens": 0} in out.log["dropped"]


async def test_privacy_layer_drops_denied_sources(monkeypatch):
    from config import load_config, Privacy
    def mock_load_config():
        cfg = load_config()
        cfg.privacy = Privacy(deny_sources=["notes"])
        return cfg
    monkeypatch.setattr("context.compile.load_config", mock_load_config)
    r = recipe(SourceSpec("notes", 50), SourceSpec("tasks", 40))
    sources = {
        "notes": FakeSource("notes", [item("n1", "notes")]),
        "tasks": FakeSource("tasks", [item("t1", "tasks")])
    }
    out = await compile_context(Situation("chat"), r, sources, PROFILE, now=NOW)
    assert ids(out.result) == ["t1"]
    assert {"id": "source:notes", "source": "notes", "reason": "privacy_denied_source", "tokens": 0} in out.log["dropped"]


def test_privacy_filter_helper_reports_reason():
    from config import Privacy
    from context.privacy import apply_privacy_filter, denied_reason
    rules = Privacy(deny_sources=["history"], deny_tags=["journal"])
    assert denied_reason("notes", ("journal",), rules) == "privacy_denied_tag"
    assert denied_reason("history", (), rules) == "privacy_denied_source"
    assert denied_reason("notes", ("work",), rules) is None
    keep = Item(id="a", text="x", source="notes", tags=("work",))
    drop = Item(id="b", text="x", source="notes", tags=("journal",))
    allowed, denied = apply_privacy_filter([keep, drop], rules)
    assert allowed == [keep] and denied == [(drop, "privacy_denied_tag")]
