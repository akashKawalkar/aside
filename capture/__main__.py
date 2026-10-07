from __future__ import annotations

import argparse
import asyncio
import json
import logging
import selectors
import shutil
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import psycopg
from psycopg_pool import PoolTimeout
from config import load_config
from capture.clock import SystemClock
from capture.heartbeat import heartbeat_files, run_heartbeat
from capture.monitoring import MonitoringFlag
from capture.probe import WindowsProbe
from capture.sampler import sampler
from capture.spool import SpoolWriter, run_writer
from storage.database import make_pool
from storage import Event, insert_many
from storage import parse_spool_file
from sessions.job import sessionize_recent, sessionize_since
log = logging.getLogger("capture")


# ---------------------------------------------------------------------------
# Collector (sampler + spool writer)
# ---------------------------------------------------------------------------

async def run_collector() -> None:
    cfg = load_config()
    queue: asyncio.Queue = asyncio.Queue()

    writer = SpoolWriter(cfg.spool_dir, cfg.rotate_mb, cfg.rotate_minutes)
    writer.recover()

    monitoring = MonitoringFlag(cfg.monitoring_file)

    sampler_task = asyncio.create_task(
        sampler(
            queue,
            SystemClock(),
            WindowsProbe(),
            cfg.poll_interval,
            cfg.idle_threshold,
            cfg.max_chunk,
            {a.casefold() for a in cfg.title_blocklist_apps},
            lambda: not monitoring.enabled(),
        )
    )
    writer_task = asyncio.create_task(run_writer(queue, writer))
    # Beats even while monitoring is paused: "paused" and "not running" are different reasons for a gap.
    heartbeat_task = asyncio.create_task(run_heartbeat(heartbeat_files(cfg.spool_dir.parent)[0], cfg.data_quality.heartbeat_interval))

    try:
        done, _ = await asyncio.wait(
            {sampler_task, writer_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in done:
            t.result()  # surface a crash instead of dying silently
    finally:
        heartbeat_task.cancel()
        sampler_task.cancel()
        await asyncio.gather(sampler_task, heartbeat_task, return_exceptions=True)
        if not writer_task.done():
            await queue.join()
        writer_task.cancel()
        await asyncio.gather(writer_task, return_exceptions=True)
        writer.close()


# ---------------------------------------------------------------------------
# Ingestor (spool -> Postgres)
# ---------------------------------------------------------------------------



async def ingest_file(pool, path: Path, done: Path, dead: Path) -> int:
    events, bad = await asyncio.to_thread(parse_spool_file, path)

    n = await insert_many(pool, events)

    if bad:
        await asyncio.to_thread(
            lambda: (dead / f"{path.stem}.bad.jsonl").write_text(
                "\n".join(bad) + "\n", encoding="utf-8"
            )
        )

    await asyncio.to_thread(shutil.move, str(path), str(done / path.name))
    return n


async def sessionize_safely(pool) -> None:
    """Sessionize recent days without letting a sessionizer bug stop ingestion."""
    try:
        result = await sessionize_recent(pool)
    except (psycopg.OperationalError, PoolTimeout):
        raise  # the ingest loop already knows how to back off and retry
    except Exception:
        log.exception("sessionizing failed")
        return

    if result.created:
        log.info(
            "sessionized %d day(s), %d new session(s)",
            result.days,
            result.created,
        )


async def run_sessionize(since: date | None) -> None:
    """One-off run: recent days, or every day from `since` for a backfill."""
    pool = make_pool()
    await pool.open()

    try:
        if since is None:
            result = await sessionize_recent(pool)
        else:
            result = await sessionize_since(pool, since)
    finally:
        await pool.close()

    print(f"sessionized {result.days} day(s), {result.created} new session(s)")


async def run_prune(apply: bool) -> None:
    """Report (or, with --apply, delete) data older than the retention config allows."""
    from storage import prune
    from storage.repo.retention import rules

    cfg = load_config()
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    pool = make_pool()
    await pool.open()
    try:
        counts = await prune(pool, cfg.retention, now=now, apply=apply)
    finally:
        await pool.close()

    verb = "deleted" if apply else "would delete"
    for rule in rules(cfg.retention):
        print(f"{verb} {counts[rule.name]:>7} {rule.name} older than {rule.days} days")
    if not apply:
        print("nothing was deleted; run again with --apply to delete")


async def run_ingestor(drain: bool) -> None:
    cfg = load_config()
    pending, done, dead = (cfg.spool_dir / d for d in ("pending", "done", "dead"))
    for d in (pending, done, dead):
        d.mkdir(parents=True, exist_ok=True)

    pool = make_pool()
    await pool.open()
    delay = 1.0
    last_sessionized: float | None = None
    heartbeat_task = None
    if not drain:
        heartbeat_task = asyncio.create_task(run_heartbeat(heartbeat_files(cfg.spool_dir.parent)[1], cfg.data_quality.heartbeat_interval))

    try:
        while True:
            try:
                for f in sorted(pending.glob("*.jsonl")):
                    n = await ingest_file(pool, f, done, dead)
                    log.info("ingested %s (%d events)", f.name, n)

                # Sessions are built from ingested events, so they are
                # refreshed here, at most once per session_interval.
                if (
                    drain
                    or last_sessionized is None
                    or time.monotonic() - last_sessionized >= cfg.session_interval
                ):
                    await sessionize_safely(pool)
                    last_sessionized = time.monotonic()

                delay = 1.0
            except (psycopg.OperationalError, PoolTimeout) as e:
                if drain:
                    raise
                log.warning("database unavailable (%s), retrying in %.0fs", e, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue

            if drain:
                break
            await asyncio.sleep(cfg.ingest_interval)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        await pool.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _run_collector_sync() -> None:
    try:
        asyncio.run(run_collector())
    except KeyboardInterrupt:
        pass


def _run_sessionize_sync(since: date | None) -> None:
    try:
        asyncio.run(
            run_sessionize(since),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    except KeyboardInterrupt:
        pass


def _run_ingestor_sync(drain: bool) -> None:
    try:
        asyncio.run(
            run_ingestor(drain),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    except KeyboardInterrupt:
        pass


def _run_prune_sync(apply: bool) -> None:
    asyncio.run(run_prune(apply), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(prog="capture")
    sub = parser.add_subparsers(dest="mode", required=True)

    sub.add_parser("collect", help="run the sampler + spool writer")

    ingest_p = sub.add_parser("ingest", help="drain spool files into Postgres")
    ingest_p.add_argument(
        "--drain",
        action="store_true",
        help="process everything currently pending, then exit",
    )

    sessionize_p = sub.add_parser(
        "sessionize",
        help="build sessions from ingested events (recent days, or a backfill)",
    )
    sessionize_p.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="backfill every day from this date through today",
    )

    prune_p = sub.add_parser("prune", help="delete data older than the retention config (report only unless --apply)")
    prune_p.add_argument("--apply", action="store_true", help="actually delete; without it nothing changes")

    args = parser.parse_args()

    if args.mode == "collect":
        _run_collector_sync()
    elif args.mode == "ingest":
        _run_ingestor_sync(args.drain)
    elif args.mode == "sessionize":
        _run_sessionize_sync(args.since)
    elif args.mode == "prune":
        _run_prune_sync(args.apply)


if __name__ == "__main__":
    main()