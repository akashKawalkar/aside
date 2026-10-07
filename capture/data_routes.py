# capture/data_routes.py — read-only data-quality routes behind the settings "Data gaps" page.
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from capture.heartbeat import heartbeat_files
from config import load_config
from sessions.gaps import month_picture
from storage import last_day_record, list_heartbeats, list_monitoring_log, list_sessions_range, session_bounds

IST = ZoneInfo("Asia/Kolkata")


def make_data_router(op_ok, op_error) -> APIRouter:
    router = APIRouter()

    @router.get("/data-gaps")
    async def data_gaps(request: Request, month: str | None = None):
        cfg = load_config()
        now = datetime.now(IST)
        match = re.fullmatch(r"(\d{4})-(\d{2})", month or now.strftime("%Y-%m"))
        if not match or not 1 <= int(match.group(2)) <= 12:
            return op_error("month must look like 2026-10.")
        year, mon = int(match.group(1)), int(match.group(2))

        first = datetime(year, mon, 1, tzinfo=IST)
        last = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=IST)
        pool = request.app.state.db_pool
        first_start, last_end = await session_bounds(pool)

        picture = month_picture(
            year, mon,
            sessions=await list_sessions_range(pool, start=first, end=last),
            monitoring=await list_monitoring_log(pool, start=first, end=last),
            heartbeats=await list_heartbeats(pool, start=first, end=last),
            now=now, cfg=cfg.data_quality, first_data_at=first_start,
        )

        stale = cfg.data_quality.heartbeat_stale_after
        health = {}
        for name, beat in zip(("collector", "ingestor"), heartbeat_files(cfg.spool_dir.parent)):
            age = beat.age()
            health[name] = {"ok": beat.alive(stale), "age_seconds": None if age is None else round(age)}
        health["last_session_end"] = last_end
        health["last_day_record"] = await last_day_record(pool)
        return op_ok(data={**picture, "health": health})

    return router
