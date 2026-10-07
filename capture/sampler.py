from dataclasses import dataclass
from datetime import datetime
import asyncio
import logging

from storage import Event
from capture.privacy import redact

log = logging.getLogger(__name__)


@dataclass
class Segment:
    state: tuple[str, str | None, str | None]
    start: datetime
    last_tick: datetime

    def close(self, end: datetime) -> Event:
        kind, app, title = self.state

        return Event(
            source="laptop",
            kind=kind,
            app=app,
            window_title=title,
            ts_start=self.start,
            ts_end=end,
        )


async def sampler(
    out: asyncio.Queue,
    clock,
    probe,
    poll: float,
    idle_threshold: float,
    max_chunk: float,
    title_blocklist_apps: set[str],
    is_paused=None,
):
    """Sample the foreground window. While `is_paused()` is true nothing is
    recorded: the open segment is closed at its last tick and no new one starts."""
    seg = None

    try:
        while True:
            if is_paused is not None and is_paused():
                if seg is not None:
                    if seg.last_tick > seg.start:
                        await out.put(seg.close(end=seg.last_tick))
                    seg = None
                await asyncio.sleep(poll)
                continue

            try:
                app, title = await asyncio.to_thread(probe.get_foreground)
                idle = await asyncio.to_thread(probe.get_idle_seconds)
            except Exception:
                log.exception("probe failed, skipping tick")
                await asyncio.sleep(poll)
                continue

            state = (
                ("idle", None, None)
                if idle > idle_threshold
                else ("app_focus", app, redact(app, title, title_blocklist_apps))
            )

            now = clock.now()

            if seg:
                changed = state != seg.state
                chunk_full = (now - seg.start).total_seconds() >= max_chunk
                sleep_gap = (now - seg.last_tick).total_seconds() > 3 * poll

                if changed or chunk_full or sleep_gap:
                    end = seg.last_tick if sleep_gap else now
                    if end > seg.start:
                        await out.put(seg.close(end=end))
                    if sleep_gap:
                        gap_start = seg.last_tick
                        gap_end = now
                        if (gap_end - gap_start).total_seconds() >= 10:
                            await out.put(
                                Event(
                                    source="laptop",
                                    kind="idle",
                                    app = None,
                                    window_title = None,
                                    ts_start=gap_start,
                                    ts_end=gap_end,
                                )
                            )
                    seg = None

            if seg is None:
                seg = Segment(state=state, start=now, last_tick=now)

            seg.last_tick = now

            await asyncio.sleep(poll)
    finally:
        # on shutdown, flush the open segment so the last chunk isn't lost
        if seg is not None and seg.last_tick > seg.start:
            out.put_nowait(seg.close(end=seg.last_tick))