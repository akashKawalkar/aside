"""The top active or questioned code-derived patterns for a context compile."""
from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from config import load_config
from context.items import Item, Situation
from storage import list_observations


class ObservationsSource:
    def __init__(self, pool: AsyncConnectionPool, *, limit: int = 3):
        self.pool = pool
        self.limit = limit
        self.name = "observations"

    async def fetch(self, situation: Situation) -> list[Item]:
        rows = await list_observations(self.pool, include_dropped=False)
        rows = [row for row in rows if row["status"] in {"active", "questioned"}]
        rows.sort(key=lambda row: (row["status"] != "active", -row["occurrences"], -row["id"]))
        items = []
        for row in rows[:self.limit]:
            text = row["text"]
            if row["status"] == "questioned":
                text = f"Possibly: {text}"
            items.append(Item(
                id=f"observation:{row['id']}", text=text, source=self.name,
                priority=row["occurrences"], provenance=f"observation:{row['id']}",
                confidence=0.65 if row["status"] == "questioned" else 0.85,
                status=row["status"],
            ))
        return items


def observations_source(pool: AsyncConnectionPool) -> ObservationsSource:
    return ObservationsSource(pool, limit=load_config().patterns.max_observations)
