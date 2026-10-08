# python -m cloud [nightly|morning_retry|manual]   — what the GitHub Action runs. Exit code 1 if any step failed.
from __future__ import annotations

import asyncio
import logging
import sys

from cloud.nightly import STEPS, run_nightly
from storage.database import make_pool


async def main(kind: str) -> int:
    pool = make_pool()
    await pool.open(wait=True, timeout=30)
    try:
        result = await run_nightly(pool, kind=kind)
    finally:
        await pool.close()
    for step, outcome in result.steps.items():
        print(f"{step:14} {outcome}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    kind = sys.argv[1] if len(sys.argv) > 1 else "nightly"
    if kind not in STEPS:
        sys.exit(f"usage: python -m cloud [{'|'.join(STEPS)}]")
    if sys.platform == "win32":      # psycopg's async mode cannot use the default Proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.exit(asyncio.run(main(kind)))
