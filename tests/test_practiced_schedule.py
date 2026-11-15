"""The practiced schedule: calendar laid over the digital schedule, verdicts, outside-the-plan, plan changes. Pure code."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from digital.builder import IST, Ev, build_digital_schedule
from digital.seed import SEED_RULES
from llm.errors import LLMRateLimited
from llm.fake import FakeClient
from practiced.builder import Plan, Tag, build_practiced_schedule, plan_changes
from practiced.llmcall import BadReply, call_json
from practiced.tags import parse_tags, request_tags

DAY = date(2026, 11, 12)


def t(h, m=0, d=0):
    return datetime(2026, 11, 12 + d, h, m, tzinfo=IST)


def code(start, end):
    return Ev("laptop", "app_focus", start, end, app="Code.exe", title="a.py - proj - Visual Studio Code")


def reel(start, end):
    return Ev("android", "phone_app_focus", start, end, app="com.instagram.android")


def digital(events):
    return build_digital_schedule(DAY, events, SEED_RULES)


def plan(title, start, end, **kw):
    return Plan(1, title, start, end, **kw)


CODING = {"Deep work": Tag(("coding",)), "Workout": Tag((), observable=False)}


def one(events, p, tags=CODING):
    r = build_practiced_schedule(digital(events), [p], tags)
    return r["plan"][0]


def test_a_block_spent_on_plan_is_done():
    b = one([code(t(10), t(12))], plan("Deep work", t(10), t(12)))
    assert (b["verdict"], b["on_plan_pct"], b["on_plan_min"]) == ("DONE", 100, 120)


def test_verdict_thresholds():
    p = plan("Deep work", t(10), t(12))
    assert one([code(t(10), t(10, 50))], p)["verdict"] == "PARTIAL"      # 50 of 120 = 42%
    assert one([code(t(10), t(10, 20)), reel(t(10, 20), t(12))], p)["verdict"] == "OFF-PLAN"   # 20 of 120 = 17%
    assert one([code(t(10), t(11, 15))], p)["verdict"] == "DONE"        # 75 of 120 = 62%


def test_a_block_the_devices_cannot_see_is_no_signal_not_a_miss():
    assert one([], plan("Workout", t(7), t(8)))["verdict"] == "NO SIGNAL"
    assert one([reel(t(7), t(7, 30))], plan("Workout", t(7), t(8)))["verdict"] == "NO SIGNAL"


def test_nothing_recorded_for_an_observable_block_is_no_signal():
    assert one([], plan("Deep work", t(10), t(12)))["verdict"] == "NO SIGNAL"


def test_a_title_without_a_tag_is_untagged():
    assert one([code(t(10), t(12))], plan("Mystery", t(10), t(12)))["verdict"] == "UNTAGGED"


def test_phone_minutes_in_the_window_and_activities_are_clipped_to_it():
    b = one([code(t(9), t(13)), reel(t(11), t(11, 40))], plan("Deep work", t(10), t(12)))
    assert b["phone_min"] == 40
    coding = [a for a in b["activities"] if a["track"] == "laptop"][0]
    assert (coding["start"], coding["end"], coding["minutes"]) == ("10:00", "12:00", 120.0)


def test_idle_inside_the_window_is_counted_apart():
    ev = [code(t(10), t(10, 30)), Ev("laptop", "idle", t(10, 30), t(11)), code(t(11), t(12))]
    b = one(ev, plan("Deep work", t(10), t(12)))
    assert b["idle_min"] == 30 and b["on_plan_min"] == 90


def test_on_plan_minutes_never_exceed_the_planned_length():
    ev = [code(t(10), t(11)), Ev("android", "phone_app_focus", t(10), t(11), app="com.openai.chatgpt")]
    tags = {"Deep work": Tag(("coding", "ai_assist"))}
    assert one(ev, plan("Deep work", t(10), t(11)), tags)["on_plan_min"] == 60


def test_outside_the_plan_lists_only_long_stretches_outside_every_window():
    ev = [code(t(10), t(11)), reel(t(13, 20), t(14, 30)), reel(t(16), t(16, 15))]
    r = build_practiced_schedule(digital(ev), [plan("Deep work", t(10), t(11))], CODING)
    assert [(o["track"], o["start"], o["end"]) for o in r["outside"]] == [("phone", "13:20", "14:30")]


def test_a_stretch_half_inside_a_window_counts_only_its_outside_part():
    r = build_practiced_schedule(digital([reel(t(10), t(11, 40))]), [plan("Deep work", t(10), t(11))], CODING)
    assert [(o["start"], o["end"], o["minutes"]) for o in r["outside"]] == [("11:00", "11:40", 40.0)]


def test_totals_and_tasks_are_carried_through():
    r = build_practiced_schedule(digital([code(t(10), t(11))]), [], {}, tasks_done=[{"text": "x", "at": "10:00"}])
    assert r["totals"]["laptop"]["coding"] == 60 and r["tasks"]["done"][0]["text"] == "x"


# --- generated vs final ---------------------------------------------------------------------------------------------------

def entry(title, start, end, **kw):
    return {"title": title, "start_at": start.isoformat(), "end_at": end.isoformat(), "state": "accepted", **kw}


def test_same_instants_in_different_text_are_not_a_change():
    a = entry("Study", t(10), t(12))
    b = entry("Study", t(10), t(12))
    b["start_at"] = t(10).astimezone(__import__("datetime").timezone.utc).isoformat()   # same moment, written in UTC
    ch = plan_changes([plan("Study", t(10), t(12), origin="generated")], {"proposed": [a], "entries": [b]})
    assert ch["retimed_in_draft"] == [] and ch["kept_unedited"] == ["Study"]


def test_a_retimed_title_and_a_discarded_entry_are_reported():
    proposed = [entry("Study", t(10), t(12)), entry("Gym", t(7), t(8)), entry("Read", t(20), t(21))]
    final = [entry("Study", t(10, 30), t(12)), entry("Gym", t(7), t(8), state="discarded"), entry("Read", t(20), t(21))]
    ch = plan_changes([plan("Study", t(10, 30), t(12), origin="generated", edited_by_user=True)], {"proposed": proposed, "entries": final})
    assert ch["retimed_in_draft"] == ["Study"] and ch["discarded"] == ["Gym"] and ch["edited_after_accepting"] == ["Study"]


def test_no_draft_and_nothing_generated_means_no_section():
    assert plan_changes([plan("Mine", t(10), t(11))], None) is None


def test_user_added_blocks_are_listed():
    ch = plan_changes([plan("Mine", t(10), t(11)), plan("Gen", t(12), t(13), origin="generated")], None)
    assert ch["added_by_user"] == ["Mine"] and ch["kept_unedited"] == ["Gen"]


# --- the model side: tags, retry -------------------------------------------------------------------------------------------

def test_parse_tags_drops_unknown_categories_and_unasked_titles():
    data = {"tags": [{"title": "A", "expected": ["coding", "bogus", "system"], "observable": True},
                     {"title": "Z", "expected": ["study"]}]}
    assert parse_tags(data, ["A", "B"]) == {"A": Tag(("coding",), True)}


async def test_request_tags_sends_titles_and_reads_the_answer():
    c = FakeClient([json.dumps({"tags": [{"title": "Run", "reason": "x", "expected": [], "observable": False}]})])
    assert await request_tags(c, ["Run"]) == {"Run": Tag((), False)}
    assert json.loads(c.calls[0].messages[-1].content) == ["Run"] and "Example output" in c.calls[0].messages[0].content


class Flaky:
    def __init__(self, fail, then):
        self.fail, self.then, self.n = fail, then, 0
        self.profile = FakeClient().profile

    async def complete(self, messages, **kw):
        self.n += 1
        if self.n <= self.fail:
            raise LLMRateLimited("busy")
        return await FakeClient([self.then]).complete(messages, **kw)


async def _no_sleep(_):
    return None


async def test_a_busy_model_is_retried_then_succeeds():
    c = Flaky(2, '{"ok": 1}')
    data, _ = await call_json(c, [], max_tokens=10, sleep=_no_sleep)
    assert data == {"ok": 1} and c.n == 3


async def test_three_failures_raise():
    c = Flaky(5, "{}")
    with pytest.raises(LLMRateLimited):
        await call_json(c, [], max_tokens=10, sleep=_no_sleep)
    assert c.n == 3


async def test_a_reply_that_is_not_json_is_not_retried_blindly():
    c = Flaky(0, "sorry, no")
    with pytest.raises(BadReply):
        await call_json(c, [], max_tokens=10, sleep=_no_sleep)
    assert c.n == 1


def test_json_inside_a_code_fence_is_read():
    from practiced.llmcall import parse_json
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
