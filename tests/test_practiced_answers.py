"""Task checks and debriefs land on the block they were asked about, never on a neighbour."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from practiced.builder import Plan, plan_block

IST = timezone(timedelta(hours=5, minutes=30))
EMPTY = {"laptop": [], "phone": []}


def at(hh, mm=0):
    return datetime(2001, 1, 10, hh, mm, tzinfo=IST)


def ms(t):
    return int(t.timestamp() * 1000)


def check(title, start, status, asked):
    return {"kind": "task_check", "answered_at": asked, "payload": {"title": title, "status": status, "block_start": ms(start)}}


def test_a_task_check_goes_to_its_own_block_not_the_one_before():
    a = Plan(1, "Email", at(10, 30), at(11))
    b = Plan(2, "Report", at(11), at(12))
    answers = [check("Report", at(11), "starting_now", at(10, 50))]
    assert "task_check" not in plan_block(a, None, EMPTY, answers)
    assert plan_block(b, None, EMPTY, answers)["task_check"]["status"] == "starting_now"


def test_two_blocks_with_the_same_title_keep_their_own_answers():
    first = Plan(1, "Deep work", at(9), at(11))
    second = Plan(2, "Deep work", at(14), at(16))
    answers = [
        {"kind": "debrief", "answered_at": at(11, 5), "payload": {"title": "Deep work", "outcome": "paused", "block_end": ms(at(11))}},
        {"kind": "debrief", "answered_at": at(16, 5), "payload": {"title": "Deep work", "outcome": "finished", "block_end": ms(at(16))}},
    ]
    assert plan_block(first, None, EMPTY, answers)["debrief"]["outcome"] == "paused"
    assert plan_block(second, None, EMPTY, answers)["debrief"]["outcome"] == "finished"


def test_the_newest_answer_wins_and_one_without_a_time_matches_by_title():
    b = Plan(1, "Report", at(11), at(12))
    answers = [check("Report", at(11), "blocked", at(10, 50)), check("Report", at(11), "starting_now", at(10, 55))]
    assert plan_block(b, None, EMPTY, answers)["task_check"]["status"] == "starting_now"
    loose = [{"kind": "task_check", "answered_at": at(10, 50), "payload": {"title": "Report", "status": "not_today"}}]
    assert plan_block(b, None, EMPTY, loose)["task_check"]["status"] == "not_today"
