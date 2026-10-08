from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from review.email import EmailResult
from review.generate import ReviewGenerator, format_duration, format_signed
from review.runner import run_review_once
from review.store import ReviewSettings, ReviewStore

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 5)  # a Monday


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def session(start, end, app="Code.exe", kind="app"):
    return {"kind": kind, "app": app, "started_at": start, "ended_at": end,
            "active_seconds": 0, "event_count": 1}


def entry(start, end, title="Focus"):
    return {"title": title, "start_at": start, "end_at": end}


def make_generator(*, sessions=(), schedule=(), completed=(), notes=0, due=()):
    async def fetch_sessions(s, e):
        return [x for x in sessions if x["started_at"] < e and x["ended_at"] > s]

    async def fetch_schedule(s, e):
        return [x for x in schedule if x["start_at"] < e and x["end_at"] > s]

    async def fetch_completed(s, e):
        return list(completed)

    async def fetch_notes(s, e):
        return notes

    async def fetch_due(s, e):
        return [x for x in due if s <= x["due_at"] < e]

    return ReviewGenerator(
        fetch_sessions=fetch_sessions,
        fetch_schedule=fetch_schedule,
        fetch_completed_tasks=fetch_completed,
        fetch_notes_count=fetch_notes,
        fetch_due_tasks=fetch_due,
    )


def test_formatting():
    assert format_duration(5400) == "1h 30m"
    assert format_duration(3600) == "1h"
    assert format_duration(600) == "10m"
    assert format_signed(-4800) == "-1h 20m"
    assert format_signed(900) == "+15m"
    assert format_signed(20) == "on plan"


async def test_quiet_day_is_brief():
    review = await make_generator().generate(review_date=DAY)

    assert review.data["quiet"]
    assert "Quiet day" in review.text
    assert "Notes" not in review.text and "Timeline" not in review.text


async def test_wall_clock_ignores_idle_and_counts_overlap_once():
    gen = make_generator(sessions=[
        session(at(DAY, 9), at(DAY, 10)),
        session(at(DAY, 9, 30), at(DAY, 10, 30), app="chrome.exe"),
        session(at(DAY, 12), at(DAY, 13), kind="idle", app=None),
    ])
    review = await gen.generate(review_date=DAY)

    assert review.data["stats"]["wall_clock_seconds"] == 90 * 60
    assert "Wall-clock time: 1h 30m" in review.text
    assert "idle" not in review.text.lower()
    assert "exe" not in review.text


async def test_plan_difference_only_when_planned():
    sessions = [session(at(DAY, 9), at(DAY, 10))]
    none = await make_generator(sessions=sessions).generate(review_date=DAY)
    assert none.data["stats"]["difference_seconds"] is None
    assert "Plan:" not in none.text

    planned = await make_generator(
        sessions=sessions, schedule=[entry(at(DAY, 9), at(DAY, 11))]
    ).generate(review_date=DAY)
    assert planned.data["stats"]["difference_seconds"] == -3600
    assert "Plan: 2h (-1h)" in planned.text


async def test_timeline_has_planned_and_actual_side_by_side():
    review = await make_generator(
        sessions=[session(at(DAY, 9, 5), at(DAY, 9, 50))],
        schedule=[entry(at(DAY, 9), at(DAY, 10), "Standup")],
    ).generate(review_date=DAY)

    kinds = [row["kind"] for row in review.data["timeline"]]
    assert kinds == ["planned", "actual"]
    assert "Planned: Standup" in review.text and "Actual: Code" in review.text


async def test_session_crossing_midnight_is_clipped_to_the_day():
    review = await make_generator(
        sessions=[session(at(DAY, 23), at(DAY + timedelta(days=1), 1))]
    ).generate(review_date=DAY)

    assert review.data["stats"]["wall_clock_seconds"] == 3600


async def test_notes_only_as_a_count_and_only_if_any():
    assert "Notes" not in (await make_generator(notes=0).generate(review_date=DAY)).text
    review = await make_generator(notes=3).generate(review_date=DAY)
    assert "Notes: 3" in review.text and not review.data["quiet"]


async def test_completed_tasks_and_tomorrow_but_never_overdue():
    tomorrow = DAY + timedelta(days=1)
    gen = make_generator(
        completed=[{"id": 1, "text": "Pay rent", "completed_at": at(DAY, 11)}],
        schedule=[entry(at(tomorrow, 9), at(tomorrow, 10), "Gym")],
        due=[
            {"id": 2, "text": "Call mum", "due_at": at(tomorrow, 18)},
            {"id": 3, "text": "Old thing", "due_at": at(DAY - timedelta(days=2), 18)},
        ],
    )
    review = await gen.generate(review_date=DAY)

    assert "Tasks completed: 1" in review.text and "- Pay rent" in review.text
    assert "Gym" in review.text and "Call mum" in review.text
    assert "Old thing" not in review.text
    assert "Gym" not in "\n".join(r["label"] for r in review.data["timeline"])


async def test_a_failing_source_does_not_block_the_review():
    gen = make_generator(notes=2)

    async def boom(s, e):
        raise RuntimeError("db down")

    gen.fetch_sessions = boom
    review = await gen.generate(review_date=DAY)

    assert "Notes: 2" in review.text


async def test_period_summary():
    gen = make_generator(sessions=[session(at(DAY, 9), at(DAY, 11))])
    review = await gen.generate_period(first=DAY, last=DAY + timedelta(days=6))

    assert review.data["scope"] == "period"
    assert review.data["stats"]["wall_clock_seconds"] == 7200
    assert "By day" in review.text


# ---- settings and runner ---------------------------------------------------


async def test_settings_validation(tmp_path):
    store = ReviewStore(tmp_path)

    assert await store.settings() == ReviewSettings()
    with pytest.raises(ValueError):
        await store.save_settings(ReviewSettings(email_enabled=True))
    with pytest.raises(ValueError):
        await store.save_settings(ReviewSettings(frequency="hourly"))
    with pytest.raises(ValueError):
        await store.save_settings(ReviewSettings(time="9pm"))

    saved = await store.save_settings(
        ReviewSettings(email_enabled=True, frequency="weekly", recipient="a@b.co", time="22:00")
    )
    assert await store.settings() == saved


async def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "review_settings.json").write_text("{not json")

    assert await ReviewStore(tmp_path).settings() == ReviewSettings()


class Sender:
    def __init__(self):
        self.sent = []

    async def __call__(self, review, *, config):
        self.sent.append(review)
        return EmailResult(ok=True, message="ok")


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("review.email.load_dotenv", lambda: None)


async def test_runner_writes_once_in_the_window_and_skips_outside(tmp_path):
    store, gen = ReviewStore(tmp_path), make_generator(notes=1)

    assert await run_review_once(gen, store, now=at(DAY, 21, 0)) is None   # too early
    assert await run_review_once(gen, store, now=at(DAY, 23, 45)) is None  # asleep too long: skipped
    assert await store.latest() is None

    assert await run_review_once(gen, store, now=at(DAY, 21, 31)) is not None
    assert (await store.latest())["date"] == DAY.isoformat()
    assert await run_review_once(gen, store, now=at(DAY, 21, 40)) is None  # already written


async def test_email_follows_interval(tmp_path, smtp):
    store, gen, sender = ReviewStore(tmp_path), make_generator(), Sender()
    await store.save_settings(ReviewSettings(email_enabled=True, frequency="weekly", recipient="a@b.co"))

    monday, sunday = DAY, DAY + timedelta(days=6)
    await run_review_once(gen, store, now=at(monday, 21, 31), send=sender)
    assert sender.sent == []

    await run_review_once(gen, store, now=at(sunday, 21, 31), send=sender)
    assert len(sender.sent) == 1 and sender.sent[0].data["scope"] == "period"


async def test_monthly_sends_on_last_day_only(tmp_path, smtp):
    store, gen, sender = ReviewStore(tmp_path), make_generator(), Sender()
    await store.save_settings(ReviewSettings(email_enabled=True, frequency="monthly", recipient="a@b.co"))

    await run_review_once(gen, store, now=at(date(2026, 10, 30), 21, 31), send=sender)
    assert sender.sent == []
    await run_review_once(gen, store, now=at(date(2026, 10, 31), 21, 31), send=sender)
    assert len(sender.sent) == 1


async def test_email_off_or_unconfigured_sends_nothing_and_still_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setattr("review.email.load_dotenv", lambda: None)
    store, gen, sender = ReviewStore(tmp_path), make_generator(), Sender()

    await run_review_once(gen, store, now=at(DAY, 21, 31), send=sender)
    assert sender.sent == [] and await store.latest()

    await store.save_settings(ReviewSettings(email_enabled=True, recipient="a@b.co"))
    await run_review_once(gen, store, now=at(DAY + timedelta(days=1), 21, 31), send=sender)
    assert sender.sent == []  # no SMTP_HOST: skipped quietly
