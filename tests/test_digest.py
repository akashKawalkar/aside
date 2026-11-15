"""The daily digest: prompt, validation, assembly, retry, and the run-once job. A FakeClient stands in for the model."""
from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

import storage
from digital.builder import IST
from llm.fake import FakeClient
from practiced import store
from practiced.builder import Tag
from practiced.digest import SYSTEM, DigestError, assemble, generate_digest, render, validate
from practiced.gold import GOLD_DAY, GOLD_OUTPUT


def test_the_gold_answer_passes_its_own_validator():
    assert validate(GOLD_OUTPUT, GOLD_DAY) == []


def test_the_prompt_carries_the_worked_example_and_the_rules():
    assert render(GOLD_DAY) in SYSTEM and json.dumps(GOLD_OUTPUT, ensure_ascii=False) in SYSTEM
    assert "never recompute" in SYSTEM and "cannot see" in SYSTEM


def test_render_shows_verdicts_but_no_percentage_for_unobservable_blocks():
    text = render(GOLD_DAY)
    assert '"AI Engineering Study Block" [generated, unedited] VERDICT OFF-PLAN on-plan 40m of 180m (22%)' in text
    assert '"Morning Workout" [generated, unedited] VERDICT NO SIGNAL | idle' in text
    assert "LATE-NIGHT" in text and "outside the plan" in text


def test_assemble_takes_every_number_and_verdict_from_code_not_the_model():
    said = copy.deepcopy(GOLD_OUTPUT)
    said["plan_blocks"][2]["note"] = "Barely followed."
    d = assemble(said, GOLD_DAY)
    study = d["facts"]["plan_blocks"][2]
    assert (study["verdict"], study["on_plan_pct"], study["note"]) == ("OFF-PLAN", 22, "Barely followed.")
    assert "on_plan_pct" not in d["facts"]["plan_blocks"][0]                       # NO SIGNAL carries no percentage
    assert [s["minutes"] for s in d["facts"]["time_sinks"]] == [350, 370, 160]
    assert d["title_tags"] == [{"label": "laptop: obsidian.exe", "category": "work_admin"}]


def test_title_tags_for_labels_that_were_not_asked_or_bad_categories_are_dropped():
    said = copy.deepcopy(GOLD_OUTPUT)
    said["title_tags"] = [{"label": "laptop: invented.exe", "category": "coding"}, {"label": "laptop: obsidian.exe", "category": "bogus"}]
    assert assemble(said, GOLD_DAY)["title_tags"] == []


def test_validator_lists_each_problem():
    bad = copy.deepcopy(GOLD_OUTPUT)
    bad["plan_blocks"] = bad["plan_blocks"][:3]
    bad["time_sinks"][1]["what"] = "something else"
    bad["summary"] = ""
    bad["anomalies"] = ["a"] * 5
    problems = validate(bad, GOLD_DAY)
    assert len(problems) == 4 and any("exactly 4" in p for p in problems) and any("something" not in p and "time_sinks entry 2" in p for p in problems)


def test_a_best_stretch_is_not_allowed_when_the_report_has_none():
    day = copy.deepcopy(GOLD_DAY)
    day["best_stretch"] = None
    assert any("best_stretch must be null" in p for p in validate(GOLD_OUTPUT, day))


async def test_a_valid_answer_is_used_at_once():
    c = FakeClient([json.dumps(GOLD_OUTPUT)])
    d = await generate_digest(c, GOLD_DAY)
    assert len(c.calls) == 1 and d["summary"].startswith("The plan was accepted")
    assert c.calls[0].messages[1].content == render(GOLD_DAY)


async def test_a_bad_answer_is_re_asked_with_the_problems_listed_then_accepted():
    bad = copy.deepcopy(GOLD_OUTPUT)
    bad["summary"] = ""
    c = FakeClient([json.dumps(bad), json.dumps(GOLD_OUTPUT)])
    d = await generate_digest(c, GOLD_DAY)
    assert len(c.calls) == 2 and d["facts"]["plan_blocks"]
    again = c.calls[1].messages
    assert again[-2].role == "assistant" and "summary must be a string" in again[-1].content


async def test_text_that_is_not_json_is_re_asked_too():
    c = FakeClient(["Sure! Here is the digest.", json.dumps(GOLD_OUTPUT)])
    await generate_digest(c, GOLD_DAY)
    assert len(c.calls) == 2 and "Sure!" in c.calls[1].messages[-2].content


async def test_after_two_retries_it_raises_and_makes_no_template():
    c = FakeClient([json.dumps({"summary": "x"})])
    with pytest.raises(DigestError):
        await generate_digest(c, GOLD_DAY)
    assert len(c.calls) == 3
