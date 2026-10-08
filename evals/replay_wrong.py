"""Replay the context behind saved ``wrong:`` marks without making model calls."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import storage
from context.recipe import load_recipe
from llm.client import load_profile
from llm.replay import replay_compile


async def collect_replays(*, recipe_name: str | None = None, include_text: bool = False) -> list[dict[str, Any]]:
    pool = storage.make_pool()
    await pool.open()
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, ts, compile_log_id, reason FROM mark_wrong_log "
                    "WHERE compile_log_id IS NOT NULL ORDER BY id"
                )
                marks = await cur.fetchall()
        results = []
        seen: set[int] = set()
        for mark_id, marked_at, compile_id, reason in marks:
            if compile_id in seen:
                continue
            seen.add(compile_id)
            stored = await storage.get_compile_log(pool, compile_id)
            if stored is None:
                results.append({"mark_id": mark_id, "compile_log_id": compile_id, "error": "compile log not found"})
                continue
            selected_recipe = load_recipe(recipe_name or stored["recipe_name"])
            try:
                profile = load_profile(stored["model"])
            except ValueError as exc:
                results.append({"mark_id": mark_id, "compile_log_id": compile_id, "error": str(exc)})
                continue
            replay = replay_compile(stored, selected_recipe, profile)
            row = {
                "mark_id": mark_id,
                "marked_at": marked_at.isoformat() if marked_at else None,
                "compile_log_id": compile_id,
                "reason": reason,
                "recipe": selected_recipe.name,
                "chosen_ids": [item.id for item in replay.result.chosen],
                "only_in_original": replay.only_in_original,
                "only_in_replay": replay.only_in_replay,
                "dropped": [item.to_dict() for item in replay.result.dropped],
            }
            if include_text:
                row["query"] = stored.get("query")
                row["context"] = replay.text
            results.append(row)
        return results
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", help="replay every case under this recipe; defaults to each original recipe")
    parser.add_argument("--include-text", action="store_true", help="include query and rebuilt context in the local report")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    rows = asyncio.run(collect_replays(recipe_name=args.recipe, include_text=args.include_text))
    report = {"mode": "context_replay_only", "model_calls": 0, "cases": rows}
    print(f"Replayed {len(rows)} distinct wrong-marked compile(s); no model calls were made.")
    for row in rows:
        if row.get("error"):
            print(f"  compile {row['compile_log_id']}: {row['error']}")
        else:
            print(f"  compile {row['compile_log_id']} (mark {row['mark_id']}): "
                  f"{len(row['chosen_ids'])} context items; "
                  f"{len(row['only_in_original'])} removed, {len(row['only_in_replay'])} added")
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
