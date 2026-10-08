"""Schedule generation, pure parts: the validator (code checks everything the model proposes), the reply parser, free
slots, the rule-based placeholder, and the first-draft-vs-final comparison. No database, no model."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from knowledge.schedule import MAX_TITLE_LENGTH as KNOWLEDGE_MAX_TITLE
from schedule_gen import placeholder
from schedule_gen.compare import draft_vs_final
from schedule_gen.gather import GenInputs
from schedule_gen.model import (
    ACCEPTED, DISCARDED, IST, MAX_TITLE_LENGTH, PENDING, Block, DraftEntry, Rules, free_slots, settled_status, tomorrow,
)
from schedule_gen.parse import ParseError, parse_entries
from schedule_gen.validator import validate

DAY = date(2026, 10, 8)
RULES = Rules(day=DAY)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def entry(title, h1, h2, m1=0, m2=0, **kw):
    return DraftEntry(title, at(h1, m1), at(h2, m2), **kw)


def reasons(rejected):
    return {r["entry"]["title"]: r["reason"] for r in rejected}


def test_title_limit_matches_the_real_schedule_limit():
    assert MAX_TITLE_LENGTH == KNOWLEDGE_MAX_TITLE


def test_tomorrow_is_by_the_ist_calendar_not_utc():
    # 20:00 UTC on 7 Oct is already 01:30 on 8 Oct in IST, so "tomorrow" is the 9th; 18:00 UTC is 23:30 IST on the 7th.
    assert tomorrow(datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)) == date(2026, 10, 9)
    assert tomorrow(datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)) == date(2026, 10, 8)
    assert tomorrow(at(0, 5, day=date(2026, 10, 7))) == date(2026, 10, 8)


# ---------- validator ----------

def test_a_good_draft_passes_and_comes_back_in_time_order():
    valid, rejected = validate([entry("Deep work", 9, 12), entry("Gym", 7, 8)], RULES)
    assert [e.title for e in valid] == ["Gym", "Deep work"] and rejected == []


@pytest.mark.parametrize("make,reason", [
    (lambda: entry("", 9, 10), "empty_title"),
    (lambda: entry("x" * 201, 9, 10), "title_too_long"),
    (lambda: DraftEntry("naive", datetime(2026, 10, 8, 9), datetime(2026, 10, 8, 10)), "bad_times"),
    (lambda: entry("backwards", 10, 9), "bad_times"),
    (lambda: DraftEntry("other day", at(9, day=date(2026, 10, 9)), at(10, day=date(2026, 10, 9))), "wrong_day"),
    (lambda: DraftEntry("overnight", at(22), at(1, day=date(2026, 10, 9))), "wrong_day"),
    (lambda: entry("too early", 5, 6), "outside_waking_hours"),
    (lambda: entry("too late", 22, 23, m2=30), "outside_waking_hours"),
    (lambda: entry("blink", 9, 9, m2=10), "too_short"),
    (lambda: entry("marathon", 7, 22), "too_long"),
])
def test_each_rule_rejects_with_its_reason(make, reason):
    valid, rejected = validate([make()], RULES)
    assert valid == [] and rejected[0]["reason"] == reason


def test_the_waking_window_is_inclusive_at_its_edges():
    valid, _ = validate([entry("first", 7, 8), entry("last", 22, 23)], RULES)
    assert len(valid) == 2


def test_nothing_may_overlap_a_fixed_block_but_touching_is_fine():
    fixed = [Block(at(9), at(10), "Standup")]
    valid, rejected = validate([entry("clash", 9, 10, m1=30, m2=30), entry("after", 10, 11), entry("before", 8, 9)], RULES, fixed)
    assert [e.title for e in valid] == ["before", "after"]
    assert reasons(rejected) == {"clash": "overlaps_fixed:Standup"}


def test_of_two_overlapping_proposals_the_earlier_wins():
    valid, rejected = validate([entry("B", 9, 11, m1=30), entry("A", 9, 10, m2=45)], RULES)
    assert [e.title for e in valid] == ["A"] and reasons(rejected) == {"B": "overlaps_draft:A"}


def test_there_is_a_cap_on_how_many_blocks_a_draft_can_have():
    many = [DraftEntry(f"e{i}", at(7) + timedelta(minutes=15 * i), at(7) + timedelta(minutes=15 * (i + 1))) for i in range(24)]
    valid, rejected = validate(many, Rules(day=DAY, max_entries=5))
    assert len(valid) == 5 and {r["reason"] for r in rejected} == {"too_many"}


def test_a_custom_waking_window_is_respected():
    rules = Rules(day=DAY, waking_start=time(9, 0), waking_end=time(18, 0))
    valid, rejected = validate([entry("early", 8, 9), entry("ok", 9, 10)], rules)
    assert [e.title for e in valid] == ["ok"] and reasons(rejected) == {"early": "outside_waking_hours"}


# ---------- parser ----------

def test_parse_reads_a_plain_reply():
    entries, rejected = parse_entries('{"entries": [{"title": "Gym", "start": "07:00", "end": "08:00", "reason": "your routine"}]}', DAY)
    assert rejected == [] and (entries[0].title, entries[0].start_at, entries[0].end_at, entries[0].reason) == ("Gym", at(7), at(8), "your routine")


def test_parse_survives_code_fences_and_chatter():
    reply = 'Sure! Here you go:\n```json\n{"entries": [{"title": "Walk", "start": "8:30", "end": "09:00"}]}\n```\nAnything else?'
    entries, _ = parse_entries(reply, DAY)
    assert [(e.title, e.start_at) for e in entries] == [("Walk", at(8, 30))]


def test_a_missing_end_defaults_to_thirty_minutes():
    entries, _ = parse_entries('{"entries": [{"title": "Call", "start": "15:00"}]}', DAY)
    assert entries[0].end_at - entries[0].start_at == timedelta(minutes=30)


def test_a_bare_list_and_iso_times_are_accepted():
    entries, _ = parse_entries('[{"title": "A", "start": "2026-10-08T09:00:00+05:30", "end": "2026-10-08T10:00:00+05:30"}]', DAY)
    assert (entries[0].start_at, entries[0].end_at) == (at(9), at(10))


def test_unreadable_items_are_reported_not_fatal():
    entries, rejected = parse_entries('{"entries": [{"title": "ok", "start": "09:00"}, {"title": "bad", "start": "25:99"}, "junk", {"start": "10:00"}]}', DAY)
    assert [e.title for e in entries] == ["ok"] and [r["reason"] for r in rejected] == ["unreadable_entry"] * 3


def test_an_empty_list_is_a_valid_answer():
    assert parse_entries('{"entries": []}', DAY) == ([], [])


@pytest.mark.parametrize("reply", ["I cannot help with that.", "", "{not json}", '{"blocks": []}', '{"entries": "none"}', "42"])
def test_a_reply_that_is_not_the_expected_shape_raises(reply):
    with pytest.raises(ParseError):
        parse_entries(reply, DAY)


# ---------- entries + statuses ----------

def test_entry_json_round_trip_keeps_ist_and_flags():
    e = entry("Gym", 7, 8, reason="routine", locked=True, edited=True, state=ACCEPTED, schedule_id=9)
    assert DraftEntry.from_json(e.to_json()) == e


def test_a_draft_settles_only_when_nothing_is_pending():
    mk = lambda *states: [DraftEntry("x", at(9), at(10), state=s) for s in states]
    assert settled_status(mk(PENDING, ACCEPTED)) is None
    assert settled_status(mk(ACCEPTED, DISCARDED)) == "accepted"
    assert settled_status(mk(DISCARDED, DISCARDED)) == "discarded"
    assert settled_status([]) == "discarded"


# ---------- free slots ----------

def test_free_slots_are_the_gaps_in_the_waking_window():
    busy = [Block(at(9), at(10)), Block(at(12), at(13))]
    assert free_slots(RULES, busy) == [(at(7), at(9)), (at(10), at(12)), (at(13), at(23))]


def test_free_slots_ignore_gaps_that_are_too_short_and_overlapping_blocks():
    busy = [Block(at(7), at(9)), Block(at(8), at(10)), Block(at(10, 15), at(23))]
    assert free_slots(RULES, busy, min_minutes=30) == []
    assert free_slots(RULES, busy, min_minutes=15) == [(at(10), at(10, 15))]


def test_an_empty_day_is_one_big_slot_and_a_full_day_has_none():
    assert free_slots(RULES, []) == [(at(7), at(23))]
    assert free_slots(RULES, [Block(at(6), at(23, 30))]) == []


# ---------- placeholder ----------

def inputs(template, fixed=(), template_day=date(2026, 10, 1)):
    return GenInputs(rules=RULES, template_day=template_day, fixed=list(fixed),
                     template=[{"title": t, "start_at": at(a, day=template_day), "end_at": at(b, day=template_day)} for t, a, b in template])


def test_placeholder_copies_last_weeks_blocks_onto_the_target_day():
    entries, rejected = placeholder.generate(inputs([("Gym", 7, 8), ("Deep work", 9, 12)]))
    assert rejected == [] and [(e.title, e.start_at, e.end_at) for e in entries] == [("Gym", at(7), at(8)), ("Deep work", at(9), at(12))]
    assert entries[0].reason == "Copied from Thu 01 Oct" and all(e.state == PENDING for e in entries)


def test_placeholder_does_not_move_or_squeeze_a_block_that_no_longer_fits():
    entries, rejected = placeholder.generate(inputs([("Gym", 7, 8), ("Deep work", 9, 12)], fixed=[Block(at(9), at(10), "Dentist")]))
    assert [e.title for e in entries] == ["Gym"] and reasons(rejected) == {"Deep work": "overlaps_fixed:Dentist"}


def test_placeholder_with_nothing_to_copy_proposes_nothing():
    assert placeholder.generate(inputs([], template_day=None)) == ([], [])


# ---------- first draft vs final ----------

def test_draft_vs_final_sorts_every_block_into_one_bucket():
    draft = [
        {"title": "Gym", "start_at": at(7).isoformat(), "end_at": at(8).isoformat()},
        {"title": "Deep work", "start_at": at(9).isoformat(), "end_at": at(12).isoformat()},
        {"title": "Read", "start_at": at(20).isoformat(), "end_at": at(21).isoformat()},
    ]
    final = [
        {"title": "gym", "start_at": at(7).isoformat(), "end_at": at(8).isoformat(), "origin": "generated"},
        {"title": "Deep work", "start_at": at(10).isoformat(), "end_at": at(13).isoformat(), "origin": "generated"},
        {"title": "Dentist", "start_at": at(15).isoformat(), "end_at": at(16).isoformat(), "origin": "user"},
    ]
    out = draft_vs_final(draft, final)
    assert out["kept"] == [{"title": "Gym"}]
    assert out["moved"] == [{"title": "Deep work", "from": at(9).isoformat(), "to": at(10).isoformat()}]
    assert [d["title"] for d in out["dropped"]] == ["Read"]
    assert out["added"] == [{"title": "Dentist", "origin": "user"}]
    assert (out["draft_size"], out["final_size"]) == (3, 3)


def test_two_blocks_with_the_same_title_are_matched_one_to_one():
    mk = lambda h: {"title": "Walk", "start_at": at(h).isoformat(), "end_at": at(h + 1).isoformat()}
    out = draft_vs_final([mk(7), mk(18)], [mk(7)])
    assert len(out["kept"]) == 1 and len(out["dropped"]) == 1 and out["added"] == []


# ---------- task blocks ----------

def test_parse_reads_task_id_and_ignores_junk_ids():
    text = '{"entries": [{"title": "Report", "start": "09:00", "end": "11:00", "task_id": 7},' \
           ' {"title": "B", "start": "12:00", "end": "12:30", "task_id": "7"},' \
           ' {"title": "C", "start": "13:00", "end": "13:30", "task_id": true}]}'
    entries, _ = parse_entries(text, DAY)
    assert [e.task_id for e in entries] == [7, None, None]


def test_validate_unlinks_unknown_and_repeated_task_ids_but_keeps_the_blocks():
    entries = [entry("Report", 9, 11, task_id=7), entry("Report again", 11, 12, task_id=7), entry("Ghost", 13, 14, task_id=99)]
    valid, rejected = validate(entries, RULES, task_ids={7})
    assert rejected == [] and [(e.title, e.task_id) for e in valid] == [("Report", 7), ("Report again", None), ("Ghost", None)]


def test_task_id_survives_the_json_round_trip():
    e = entry("Report", 9, 11, task_id=7)
    assert DraftEntry.from_json(e.to_json()).task_id == 7
