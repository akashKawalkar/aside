"""Patterns from practiced history (planning_design.md 2.5 and 2.6). Pure code over stored `practiced_day.data` rows.

Each pattern is a small structured claim that always carries its counts, so the planner can see how thin the evidence is.
Only days the person called normal (or did not label) are learned from; travel, sick and festival days are left out.
Nothing here calls a model, and nothing in `patterns/engine.py` is used or changed.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

OBSERVED = ("DONE", "PARTIAL", "OFF-PLAN")
LAPTOP_ON_MIN_SHARE = 0.25         # under this much laptop data in a block's window, the laptop was off: not a miss
LENGTH_BUCKETS = (("up to 90m", 0, 90), ("91-150m", 91, 150), ("over 150m", 151, 10_000))
MIN_HOUR_ACTIVE_MIN, MIN_HOUR_DAYS = 60, 3
RECENT_DAYS = 14
THIN = 5                           # fewer observations than this is called thin


def _day(d: dict) -> date:
    return date.fromisoformat(d["day"])


def normal_days(days: list[dict]) -> list[dict]:
    return [d for d in days if d.get("context", "normal") == "normal"]


def observed_blocks(days: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Blocks that count toward adherence, and how many were left out and why (the data-health gate)."""
    kept, out = [], {"laptop_off": 0, "no_signal": 0, "untagged": 0}
    for d in days:
        for b in d["plan"]:
            if b["verdict"] == "NO SIGNAL":
                out["no_signal"] += 1
            elif b["verdict"] == "UNTAGGED":
                out["untagged"] += 1
            elif b.get("laptop_data_min", 0) < LAPTOP_ON_MIN_SHARE * max(b["planned_min"], 1):
                out["laptop_off"] += 1
            else:
                kept.append(b)
    return kept, out


def _pct(values: list[float], q: float) -> float:
    v = sorted(values)
    if not v:
        return 0.0
    i = (len(v) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def adherence_by_length(days: list[dict]) -> dict:
    kept, left_out = observed_blocks(normal_days(days))
    rows = []
    for name, lo, hi in LENGTH_BUCKETS:
        got = [b["on_plan_pct"] for b in kept if lo <= b["planned_min"] <= hi]
        rows.append({"length": name, "n": len(got), "median_on_plan_pct": round(median(got)) if got else None})
    return {"rows": rows, "left_out": left_out}


def hours(days: list[dict]) -> dict:
    active: dict[str, float] = {}
    productive: dict[str, float] = {}
    seen: dict[str, int] = {}
    for d in normal_days(days):
        for h, v in d.get("stats", {}).get("by_hour", {}).items():
            active[h] = active.get(h, 0) + v["active"]
            productive[h] = productive.get(h, 0) + v["productive"]
            seen[h] = seen.get(h, 0) + 1
    rows = [{"hour": h, "productive_share": round(100 * productive[h] / active[h]), "active_min": round(active[h]), "days": seen[h]}
            for h in sorted(active) if active[h] >= MIN_HOUR_ACTIVE_MIN and seen[h] >= MIN_HOUR_DAYS]
    ranked = sorted(rows, key=lambda r: (-r["productive_share"], r["hour"]))
    return {"best": ranked[:3], "worst": [r for r in reversed(ranked[-3:]) if r not in ranked[:3]]}


def run_lengths(days: list[dict]) -> dict:
    runs = [r for d in normal_days(days) for r in d.get("stats", {}).get("productive_runs", [])]
    if not runs:
        return {"n": 0}
    return {"n": len(runs), "median": round(median(runs)), "p75": round(_pct(runs, 0.75)), "p90": round(_pct(runs, 0.9)),
            "share_60_plus": round(100 * sum(r >= 60 for r in runs) / len(runs)), "max": max(runs)}


def phone_co_use(days: list[dict]) -> dict:
    rows = [d["stats"] for d in normal_days(days) if d.get("stats", {}).get("productive_laptop_min", 0) > 0]
    if not rows:
        return {"n_days": 0}
    shares = [100 * r["phone_over_productive_min"] / r["productive_laptop_min"] for r in rows]
    return {"n_days": len(rows), "median_share_pct": round(median(shares)),
            "median_minutes_per_day": round(median(r["phone_over_productive_min"] for r in rows))}


def _phone_play(d: dict) -> float:
    ph = d["totals"]["phone"]
    return ph.get("social", 0) + ph.get("media", 0)


def trend(days: list[dict], today: date) -> dict:
    """The last 14 days against the 14 before, per day, on normal days only."""
    def window(a: int, b: int) -> list[dict]:
        return [d for d in normal_days(days) if today - timedelta(days=b) <= _day(d) < today - timedelta(days=a)]
    now, before = window(0, RECENT_DAYS), window(RECENT_DAYS, 2 * RECENT_DAYS)

    def avg(ds: list[dict], f) -> int | None:
        return round(sum(f(d) for d in ds) / len(ds)) if ds else None
    return {
        "recent": {"days": len(now), "productive_laptop_min": avg(now, lambda d: d["stats"]["productive_laptop_min"]),
                   "phone_social_media_min": avg(now, _phone_play)},
        "before": {"days": len(before), "productive_laptop_min": avg(before, lambda d: d["stats"]["productive_laptop_min"]),
                   "phone_social_media_min": avg(before, _phone_play)},
        "not_learned_from": sorted(d["day"] for d in days if d.get("context", "normal") != "normal"
                                   and today - timedelta(days=2 * RECENT_DAYS) <= _day(d) < today),
    }


def data_health(days: list[dict], today: date) -> dict:
    recent = [d for d in days if today - timedelta(days=RECENT_DAYS) <= _day(d) < today]
    _, left_out = observed_blocks(normal_days(recent))
    counted = sum(len([b for b in d["plan"] if b["verdict"] != "NO SIGNAL"]) for d in normal_days(recent))
    share = round(100 * left_out["laptop_off"] / counted) if counted else 0
    return {"days": len(recent), "blocks_with_laptop_off": left_out["laptop_off"], "of_blocks": counted, "laptop_off_pct": share,
            "flag": counted >= THIN and share >= 40}


def _tod(hm: str) -> int:
    """Minutes since 18:00, so a bedtime either side of midnight sorts on one line."""
    h, m = map(int, hm.split(":"))
    return (h * 60 + m - 18 * 60) % 1440


def _hm(minutes_since_18: int) -> str:
    t = (minutes_since_18 + 18 * 60) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"


def sleep_window(days: list[dict]) -> dict:
    rows = [d["sleep"] for d in normal_days(days) if d.get("sleep")]
    if not rows:
        return {"n": 0}
    return {"n": len(rows), "inferred": all(r.get("inferred") for r in rows), "median_bedtime": _hm(round(median(_tod(r["start"]) for r in rows))),
            "median_wake": _hm(round(median(_tod(r["wake"]) for r in rows))), "median_minutes": round(median(r["minutes"] for r in rows))}


def daily_productive(days: list[dict]) -> dict:
    """Productive laptop minutes on days the laptop was used at all."""
    mins = [d["stats"]["productive_laptop_min"] for d in normal_days(days) if d.get("stats", {}).get("by_hour")]
    return {"n": len(mins), "median_min": round(median(mins)) if mins else 0, "p75_min": round(_pct(mins, 0.75)) if mins else 0}


def _plain_work(label: str) -> str:
    """Device labels as plain descriptions, so the planner names work by what it is and not by the app it happened in."""
    if label.startswith("VS Code: "):
        return f"coding project {label[9:]}"
    if label.startswith("ChatGPT: "):
        topic = label[9:]
        return f"topic worked on in ChatGPT: {topic}" if len(topic) <= 40 else ""        # a long one is a pasted prompt, not a title
    if label.startswith("PDF: "):
        return f"reading {label[5:]}"
    return "" if label in ("ChatGPT", "ChatGPT (phone)") else label


def recent_work(days: list[dict], today: date, window: int = RECENT_DAYS, top: int = 8) -> list[str]:
    """What the devices show the person actually worked on lately: label and minutes, biggest first, travel days included."""
    total: dict[str, int] = {}
    for d in days:
        if today - timedelta(days=window) <= _day(d) < today:
            for k, m in d.get("stats", {}).get("work", {}).items():
                total[k] = total.get(k, 0) + m
    ranked = [(_plain_work(k), m) for k, m in sorted(total.items(), key=lambda kv: -kv[1]) if _plain_work(k)]
    return [f"{k}: {m}m" for k, m in ranked[:top]]


def compute_patterns(days: list[dict], today: date) -> dict[str, Any]:
    """`days`: practiced data dicts, oldest first. `today`: the logical day being planned for (not included in the trend)."""
    days = [d for d in days if _day(d) < today]
    return {
        "adherence_by_length": adherence_by_length(days), "hours": hours(days), "run_lengths": run_lengths(days),
        "phone_co_use": phone_co_use(days), "trend": trend(days, today), "data_health": data_health(days, today),
        "sleep": sleep_window(days), "daily_productive": daily_productive(days), "days_seen": len(days), "days_learned_from": len(normal_days(days)),
    }


def render_patterns(p: dict) -> str:
    """The patterns as short lines with their counts, for the planner. Thin evidence is said to be thin."""
    out = [f"Learned from {p['days_learned_from']} normal days of {p['days_seen']}."]
    a = p["adherence_by_length"]
    out.append("Block length vs how much of it was spent on plan (median, laptop-on blocks): " + "; ".join(
        f"{r['length']}: {str(r['median_on_plan_pct']) + '%' if r['n'] else 'no data'} (n={r['n']}{', thin' if 0 < r['n'] < THIN else ''})" for r in a["rows"])
        + f". Left out: {a['left_out']['laptop_off']} with the laptop off, {a['left_out']['no_signal']} the devices cannot see.")
    h = p["hours"]
    if h["best"]:
        out.append("Most productive clock hours: " + ", ".join(f"{r['hour']}:00 ({r['productive_share']}% of {r['active_min']}m over {r['days']} days)" for r in h["best"]) + ".")
    if h["worst"]:
        out.append("Least productive hours: " + ", ".join(f"{r['hour']}:00 ({r['productive_share']}% of {r['active_min']}m over {r['days']} days)" for r in h["worst"]) + ".")
    r = p["run_lengths"]
    if r["n"]:
        out.append(f"Productive run length: median {r['median']}m, 75th percentile {r['p75']}m, 90th {r['p90']}m, {r['share_60_plus']}% of runs 60m or longer ({r['n']} runs).")
    c = p["phone_co_use"]
    if c["n_days"]:
        out.append(f"Phone social/media overlapped productive laptop time by a median {c['median_share_pct']}% ({c['median_minutes_per_day']}m a day, {c['n_days']} days).")
    t = p["trend"]
    if t["recent"]["days"] and t["before"]["days"]:
        out.append(f"Last {RECENT_DAYS} days vs the {RECENT_DAYS} before (per day): productive laptop {t['recent']['productive_laptop_min']}m vs "
                   f"{t['before']['productive_laptop_min']}m; phone social/media {t['recent']['phone_social_media_min']}m vs {t['before']['phone_social_media_min']}m "
                   f"({t['recent']['days']} and {t['before']['days']} days).")
    if t["not_learned_from"]:
        out.append("Days not learned from (travel, sick or festival): " + ", ".join(t["not_learned_from"]) + ".")
    d = p["data_health"]
    if d["flag"]:
        out.append(f"Data health: the laptop was off for {d['laptop_off_pct']}% of recent blocks, so adherence figures are thin.")
    dp = p.get("daily_productive")
    if dp and dp["n"]:
        out.append(f"Productive laptop time per day (days the laptop was used): median {dp['median_min']}m, 75th percentile {dp['p75_min']}m ({dp['n']} days).")
    s = p["sleep"]
    if s["n"]:
        how = "inferred from the longest stretch with no input on either device" if s.get("inferred") else "the person's answers"
        out.append(f"Sleep ({how}, {s['n']} nights): bedtime about {s['median_bedtime']}, wake about {s['median_wake']}, {s['median_minutes']}m.")
    return "\n".join(out)
