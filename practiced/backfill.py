"""Build the practiced schedule of past days once, so patterns have history: python -m practiced.backfill [DAYS] [--db]

Writes `practiced_day` rows (no digests) and, for calendar titles it has not seen, makes model calls to tag them (one call
per batch of new titles). Without --db it runs against the test database; --db means DATABASE_URL, the live one.
Needs AGENT_BACKGROUND_ENABLED=1 for the tag calls, and stops if the day's call cap is used up.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(".env")
if "--db" not in sys.argv and os.environ.get("DATABASE_URL_TEST"):
    os.environ["DATABASE_URL"] = os.environ["DATABASE_URL_TEST"].strip()

import storage  # noqa: E402
from digital.builder import IST  # noqa: E402
from llm.approved import Grant, approved  # noqa: E402
from llm.background import background_client  # noqa: E402
from practiced.store import backfill  # noqa: E402


async def main() -> None:
    days = int(next((a for a in sys.argv[1:] if a.isdigit()), 30))
    pool = storage.make_pool()
    await pool.open(wait=True, timeout=10)
    try:
        with approved(Grant("background", operation="practiced-backfill")):
            print(await backfill(pool, background_client(pool), datetime.now(IST), days))
    finally:
        await pool.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
