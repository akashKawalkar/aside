"""The digital schedule builder: rules, fragments, 10-minute buckets, blocks. Pure code, no database."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from digital.builder import IST, Ev, build_digital_schedule, day_window, fragments, unmapped
from digital.rules import Rule, classify, clean_title, ordered
from digital.seed import SEED_RULES

DAY = date(2026, 11, 12)


def t(h, m=0, s=0, d=0):
    return datetime(2026, 11, 12 + d, h, m, s, tzinfo=IST)


def laptop(app, start, end, title=None):
    return Ev("laptop", "app_focus", start, end, app=app, title=title)


def browser(domain, title, start, end):
    return Ev("browser", "app_focus", start, end, domain=domain, title=title)


def phone(pkg, start, end):
    return Ev("android", "phone_app_focus", start, end, app=pkg)


def build(events):
    return build_digital_schedule(DAY, events, SEED_RULES)


# --- rules ----------------------------------------------------------------------------------------------------------

def test_the_most_specific_rule_wins():
    rs = ordered(SEED_RULES)
    assert classify(rs, "laptop", "chrome.exe", "www.youtube.com", "(3) PyTorch tutorial - YouTube").category == "study"
    assert classify(rs, "laptop", "chrome.exe", "www.youtube.com", "funny cats - YouTube").category == "media"
    assert classify(rs, "laptop", "chrome.exe", "www.youtube.com", "cats #shorts - YouTube").label.startswith("YouTube shorts")
    assert classify(rs, "laptop", "chrome.exe", "www.linkedin.com", "Search | LinkedIn").category == "job_search"
    assert classify(rs, "laptop", "chrome.exe", "www.linkedin.com", "(2) Feed | LinkedIn").category == "social"
    assert classify(rs, "laptop", "chrome.exe", "www.linkedin.com", "Jane Doe | LinkedIn").category == "networking"


def test_a_phone_rule_does_not_apply_on_the_laptop():
    rs = ordered(SEED_RULES)
    assert classify(rs, "phone", "com.google.android.youtube", None, None).category == "media"
    assert classify(rs, "laptop", "com.google.android.youtube", None, None).rule_id == 0


def test_unknown_things_are_other_with_no_rule():
    m = classify(ordered(SEED_RULES), "laptop", "weird.exe", None, "x")
    assert (m.category, m.rule_id, m.label) == ("other", 0, "weird.exe")


def test_disabled_rules_are_ignored():
    rules = [Rule("coding", "A", app="a.exe", priority=90, enabled=False, id=1), Rule("study", "B", app="a.exe", id=2)]
    assert classify(ordered(rules), "laptop", "a.exe", None, None).category == "study"


def test_titles_lose_counts_and_site_names():
    assert clean_title("(3) Great video - YouTube") == "Great video"
    assert clean_title("YouTube") == ""


def test_vs_code_label_is_project_and_file():
    m = classify(ordered(SEED_RULES), "laptop", "Code.exe", None, "builder.py - personal-agent - Visual Studio Code")
    assert m.category == "coding" and m.label == "VS Code: personal-agent (builder.py)"


# --- fragments ------------------------------------------------------------------------------------------------------

def test_a_chrome_fragment_takes_the_browser_event_it_overlaps_most():
    ev = [
        laptop("chrome.exe", t(10), t(10, 10), "x"),
        browser("chatgpt.com", "ChatGPT", t(10), t(10, 2)),
        browser("www.youtube.com", "a video - YouTube", t(10, 2), t(10, 10)),
    ]
    f = fragments(ev, SEED_RULES, *day_window(DAY))
    assert [x.category for x in f] == ["media"]


def test_idle_cuts_app_time_and_system_apps_are_dropped():
    ev = [
        laptop("Code.exe", t(10), t(10, 10), "a.py - proj - Visual Studio Code"),
        Ev("laptop", "idle", t(10, 4), t(10, 6)),
        laptop("explorer.exe", t(10, 10), t(10, 20)),
    ]
    f = fragments(ev, SEED_RULES, *day_window(DAY))
    cats = [(x.category, (x.end - x.start).seconds // 60) for x in f]
    assert ("coding", 4) in cats and ("coding", 4) in cats and ("idle", 2) in cats
    assert all(c != "system" for c, _ in cats)
    assert sum(m for c, m in cats if c == "coding") == 8


def test_events_are_clipped_to_the_0500_window():
    ev = [laptop("Code.exe", t(4, 30), t(5, 30), "a.py - proj - Visual Studio Code")]
    f = fragments(ev, SEED_RULES, *day_window(DAY))
    assert f[0].start == t(5) and f[0].end == t(5, 30)


# --- blocks ---------------------------------------------------------------------------------------------------------

def test_buckets_merge_into_one_block_and_small_buckets_are_skipped():
    ev = [
        laptop("Code.exe", t(10), t(10, 30), "a.py - proj - Visual Studio Code"),     # three full buckets
        laptop("Code.exe", t(10, 40), t(10, 41), "a.py - proj - Visual Studio Code"),  # one minute: under the 2 minute floor
    ]
    s = build(ev)
    assert len(s["laptop"]) == 1
    b = s["laptop"][0]
    assert (b["start"], b["end"]) == (t(10).isoformat(), t(10, 30).isoformat())
    assert b["category"] == "coding" and b["minutes"] == 30


def test_a_skipped_bucket_breaks_the_merge():
    ev = [laptop("Code.exe", t(10), t(10, 10), "a - p - Visual Studio Code"), laptop("Code.exe", t(10, 20), t(10, 30), "a - p - Visual Studio Code")]
    assert len(build(ev)["laptop"]) == 2


def test_majority_category_per_bucket():
    ev = [
        laptop("Code.exe", t(10), t(10, 6), "a - p - Visual Studio Code"),
        browser("www.instagram.com", "Instagram", t(10, 6), t(10, 10)),
        laptop("chrome.exe", t(10, 6), t(10, 10)),
    ]
    assert build(ev)["laptop"][0]["category"] == "coding"


def test_labels_are_kept_in_the_order_they_first_appeared_with_minutes():
    ev = [
        browser("www.youtube.com", "A - YouTube", t(10), t(10, 3)), laptop("chrome.exe", t(10), t(10, 3)),
        browser("www.youtube.com", "B - YouTube", t(10, 3), t(10, 5)), laptop("chrome.exe", t(10, 3), t(10, 5)),
        browser("www.youtube.com", "A - YouTube", t(10, 5), t(10, 9)), laptop("chrome.exe", t(10, 5), t(10, 9)),
    ]
    labels = build(ev)["laptop"][0]["labels"]
    assert [l["label"] for l in labels] == ["YouTube: A", "YouTube: B"]
    assert labels[0]["minutes"] == 7 and labels[1]["minutes"] == 2


def test_laptop_and_phone_are_separate_tracks_that_overlap():
    ev = [laptop("Code.exe", t(14), t(14, 20), "a - p - Visual Studio Code"), phone("com.instagram.android", t(14), t(14, 20))]
    s = build(ev)
    assert s["laptop"][0]["category"] == "coding" and s["phone"][0]["category"] == "social"
    assert s["totals"]["phone"]["social"] == 20


def test_late_night_flag_from_23_to_06():
    ev = [laptop("Code.exe", t(23, 10), t(23, 30), "a - p - Visual Studio Code"), laptop("Code.exe", t(22, 0), t(22, 20), "a - p - Visual Studio Code"),
          laptop("Code.exe", t(1, 0, d=1), t(1, 20, d=1), "a - p - Visual Studio Code"), laptop("Code.exe", t(6, 0), t(6, 20), "a - p - Visual Studio Code")]
    flags = {datetime.fromisoformat(b["start"]).astimezone(IST).hour: b["late_night"] for b in build(ev)["laptop"]}
    assert flags == {6: False, 22: False, 23: True, 1: True}


def test_idle_is_its_own_block():
    ev = [Ev("laptop", "idle", t(11), t(11, 25))]
    b = build(ev)["laptop"]
    assert [(x["category"], x["minutes"]) for x in b] == [("idle", 25)]


def test_unmapped_lists_only_what_no_rule_knows():
    ev = [laptop("weird.exe", t(10), t(10, 12)), laptop("Code.exe", t(10, 12), t(10, 20), "a - p - Visual Studio Code")]
    assert unmapped(DAY, ev, SEED_RULES) == [("laptop: weird.exe", 12.0)]
