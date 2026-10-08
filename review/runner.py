from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from review.email import send_review, smtp_config_from_env
from review.generate import DailyReview, ReviewGenerator
from review.store import ReviewStore

log = logging.getLogger(__name__)

# A review is only written within this long after its time. If the laptop was
# off or asleep through that window, the day is skipped, not made up late.
GRACE = timedelta(hours=2)


def _email_period(frequency: str, today):
    """First day of the period to email tonight, or None if it isn't time yet."""
    if frequency == "daily":
        return today
    if frequency == "weekly" and today.weekday() == 6:
        return today - timedelta(days=6)
    if frequency == "monthly" and (today + timedelta(days=1)).day == 1:
        return today.replace(day=1)

    return None


async def run_review_once(
    generator: ReviewGenerator,
    store: ReviewStore,
    *,
    now: datetime | None = None,
    send=send_review,
) -> DailyReview | None:
    """
    Write tonight's review if it is time and it hasn't been written yet,
    then email it if the chosen interval says so. Returns the review, or
    None when nothing was due.
    """
    now = now or datetime.now(generator.timezone)
    settings = await store.settings()
    today = now.date()

    hour, minute = map(int, settings.time.split(":"))
    due = datetime.combine(today, time(hour, minute), tzinfo=now.tzinfo)

    if not due <= now <= due + GRACE:
        return None

    latest = await store.latest()
    if latest and latest.get("date") == today.isoformat():
        return None

    review = await generator.generate(review_date=today)
    await store.save_latest(review)

    first = _email_period(settings.frequency, today)
    if settings.email_enabled and settings.recipient and first is not None:
        await _email(generator, review, first, today, settings.recipient, send)

    return review


async def _email(generator, review, first, today, recipient, send) -> None:
    config = smtp_config_from_env(recipient)

    if config is None:
        log.info("review email skipped: SMTP is not configured")
        return

    if first != today:
        review = await generator.generate_period(first=first, last=today)

    result = await send(review, config=config)

    if not result.ok:
        log.warning("review email failed: %s", result.message)


async def run_review_worker(
    generator: ReviewGenerator,
    store: ReviewStore,
    interval: float = 60.0,
) -> None:
    while True:
        try:
            if await run_review_once(generator, store):
                log.info("nightly review written")

        except asyncio.CancelledError:
            raise

        except Exception:
            log.exception("review worker failed")

        await asyncio.sleep(interval)
