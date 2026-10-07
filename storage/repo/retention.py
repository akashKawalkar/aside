# storage/repo/retention.py — apply the retention config. Report-only unless apply=True.
# Derived records (sessions, day records, snapshots, logs of edits) are never touched: only the bulky raw data.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from config import Retention


@dataclass(frozen=True)
class Rule:
    name: str
    table: str
    column: str          # the time column compared to the cutoff
    days: int

    def cutoff(self, now: datetime) -> datetime:
        return now - timedelta(days=self.days)


def rules(retention: Retention) -> list[Rule]:
    return [
        Rule("raw events", "events", "ts_end", retention.raw_events_days),
        Rule("past schedule entries", "schedule", "end_at", retention.past_schedule_days),
        Rule("llm traces", "llm_trace", "ts", retention.llm_trace_days),
        Rule("compile logs", "compile_log", "ts", retention.compile_log_days),
        Rule("heartbeat samples", "heartbeat_log", "ts", retention.heartbeat_days),
    ]


async def prune(
    pool, retention: Retention, *, now: datetime, apply: bool = False, names: set[str] | None = None
) -> dict[str, int]:
    """Rows older than each rule's cutoff: counted, and deleted only when `apply`. One transaction.
    `names` limits it to those rules (by Rule.name)."""
    counts: dict[str, int] = {}
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            for rule in rules(retention):
                if names is not None and rule.name not in names:
                    continue
                cutoff = rule.cutoff(now)
                if apply:
                    await cur.execute(f"DELETE FROM {rule.table} WHERE {rule.column} < %s", (cutoff,))
                    counts[rule.name] = cur.rowcount
                else:
                    await cur.execute(f"SELECT count(*) FROM {rule.table} WHERE {rule.column} < %s", (cutoff,))
                    counts[rule.name] = (await cur.fetchone())[0]
    return counts
