"""Real durations: how long planned blocks of each type really got (planned vs actual). A droppable module.

It only reads practiced rows and nothing else reads it, so deleting this file loses nothing. Matching is coarse on purpose:
a block's type is the first category its title is expected to produce (coding, study, ...), nothing finer.
"""
from __future__ import annotations

from statistics import median

from practiced.patterns import OBSERVED, THIN, normal_days, observed_blocks


def real_durations(days: list[dict]) -> list[dict]:
    kept, _ = observed_blocks(normal_days(days))
    by_type: dict[str, list[dict]] = {}
    for b in kept:
        if b["verdict"] in OBSERVED and b.get("expected"):
            by_type.setdefault(b["expected"][0], []).append(b)
    rows = []
    for kind, bs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        rows.append({
            "type": kind, "n": len(bs), "planned_median_min": round(median(b["planned_min"] for b in bs)),
            "actual_median_min": round(median(b["on_plan_min"] for b in bs)),
            "actual_over_planned_pct": round(100 * median(b["on_plan_min"] / b["planned_min"] for b in bs)),
        })
    return rows


def render_durations(rows: list[dict]) -> str:
    if not rows:
        return ""
    return "Planned length vs time really spent on plan, by block type (median, laptop-on blocks): " + "; ".join(
        f"{r['type']}: planned {r['planned_median_min']}m, on plan {r['actual_median_min']}m ({r['actual_over_planned_pct']}%), "
        f"n={r['n']}{', thin' if r['n'] < THIN else ''}" for r in rows) + "."
