"""Pure rules for the persistent file: separate days, eviction order."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from context.persistent_rules import eviction_order, ist_day, is_settled, pick_evictions, with_day

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def entry(id, section="identity", text="x", evidence=1, confirmed=None, created=T0, status="active"):
    return {"id": id, "section": section, "text": text, "evidence_count": evidence, "last_confirmed": confirmed,
            "created": created, "status": status}


def est(text):
    return len(text)


def test_ist_day_rolls_over_at_ist_midnight_not_utc():
    assert ist_day(datetime(2026, 10, 1, 18, 29, tzinfo=timezone.utc)) == date(2026, 10, 1)   # 23:59 IST
    assert ist_day(datetime(2026, 10, 1, 18, 30, tzinfo=timezone.utc)) == date(2026, 10, 2)   # 00:00 IST


def test_a_fact_seen_twice_in_one_day_counts_once():
    seen = with_day([date(2026, 10, 1)], T0)
    seen = with_day(seen, T0 + timedelta(hours=3))
    assert seen == [date(2026, 10, 1)]
    assert not is_settled(seen, 3)


def test_three_separate_days_settle():
    seen = []
    for d in range(3):
        seen = with_day(seen, T0 + timedelta(days=d))
    assert is_settled(seen, 3) and not is_settled(seen, 4)


def test_eviction_goes_dynamic_sections_first_then_least_evidence_then_oldest_unconfirmed():
    entries = [
        entry(1, "identity", evidence=1),
        entry(2, "goals", evidence=5),
        entry(3, "people", evidence=1),
        entry(4, "goals", evidence=1, confirmed=T0 + timedelta(days=5)),
        entry(5, "goals", evidence=1, created=T0 - timedelta(days=9)),   # never confirmed: created is its freshness, older than 3
        entry(6, "routine", evidence=1, status="retired"),
    ]
    assert [e["id"] for e in eviction_order(entries, ["goals", "people"])] == [5, 3, 4, 2, 1]


def test_pick_evictions_stops_as_soon_as_the_rest_fits_and_spares_the_new_entry():
    entries = [entry(1, "goals", text="a" * 10), entry(2, "goals", text="b" * 10), entry(3, "identity", text="c" * 10)]
    assert [e["id"] for e in pick_evictions(entries, 20, ["goals"], est)] == [1]
    assert [e["id"] for e in pick_evictions(entries, 20, ["goals"], est, frozenset({1}))] == [2]
    assert pick_evictions(entries, 30, ["goals"], est) == []


def test_nothing_is_picked_when_only_protected_entries_remain():
    entries = [entry(1, "goals", text="a" * 50)]
    assert pick_evictions(entries, 10, ["goals"], est, frozenset({1})) == []
