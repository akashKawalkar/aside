"""The practiced schedule of one day: the final calendar laid over the digital schedule. Pure code, no database, no model.

Steps (planning_design.md section 2.3):
 - for each final calendar block: on-plan minutes (device time in the categories its title is expected to produce), idle
   minutes, phone minutes in the window, and the time-ordered activity list per track, clipped to the window;
 - a verdict from the on-plan share: DONE >= 60%, PARTIAL >= 25%, else OFF-PLAN; NO SIGNAL when the devices cannot observe
   the plan (exercise, wellbeing) or recorded nothing, which is not a miss; UNTAGGED when the title has no tag yet;
 - "outside the plan": non-idle device blocks of 30+ minutes that fall outside every calendar window;
 - what changed between the generated draft and the final calendar, compared by instants, not by JSON text;
 - day totals, tasks done, tasks that keep slipping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from digital.rules import PRODUCTIVE

IST = ZoneInfo("Asia/Kolkata")
DONE_AT, PARTIAL_AT = 60, 25
OUTSIDE_MIN = 30


@dataclass(frozen=True)
class Plan:
    """One final calendar block (a `schedule` row that is not deleted)."""
    id: int
    title: str
    start: datetime
    end: datetime
    origin: str = "user"                 # user | generated
    edited_by_user: bool = False

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass(frozen=True)
class Tag:
    expected: tuple[str, ...]            # categories the devices should show during this block
    observable: bool = True              # False: the devices cannot see it (workout, meditation)


def _hm(t: datetime) -> str:
    return t.astimezone(IST).strftime("%H:%M")


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> tuple[datetime, datetime] | None:
    s, e = max(a0, b0), min(a1, b1)
    return (s, e) if e > s else None


def _scaled(block: dict, s: datetime, e: datetime) -> tuple[float, list[dict]]:
    """The block's minutes and labels, scaled to the part of it between s and e."""
    b0, b1 = datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"])
    frac = (e - s).total_seconds() / (b1 - b0).total_seconds()
    labels = [{"label": l["label"], "minutes": round(l["minutes"] * frac, 1)} for l in block["labels"]]
    return block["minutes"] * frac, [l for l in labels if l["minutes"] >= 0.5]


def _entry(block: dict, s: datetime, e: datetime) -> dict:
    minutes, labels = _scaled(block, s, e)
    return {"track": block["track"], "start": _hm(s), "end": _hm(e), "category": block["category"],
            "minutes": round(minutes, 1), "labels": labels, "late_night": block["late_night"]}


def _verdict(tag: Tag | None, pct: int, on_plan: float, any_signal: bool) -> str:
    if tag is None:
        return "UNTAGGED"
    if not any_signal or (not tag.observable and on_plan == 0):
        return "NO SIGNAL"
    return "DONE" if pct >= DONE_AT else "PARTIAL" if pct >= PARTIAL_AT else "OFF-PLAN"


def _source(p: Plan) -> str:
    if p.origin != "generated":
        return "user"
    return "generated, edited" if p.edited_by_user else "generated, unedited"


WORKOUT_WORDS = ("workout", "gym", "exercise", "run", "yoga", "walk", "swim")
MEDITATION_WORDS = ("meditat", "mindful", "pranayam")


def _reason(a: dict) -> dict | None:
    pl = a["payload"]
    if not pl.get("reason"):
        return None
    r = {"at": _hm(a["answered_at"]), "reason": pl["reason"]}
    for k in ("minutes", "apps", "text"):
        if pl.get(k):
            r[k] = pl[k]
    return r


def _self_report(title: str, tag: Tag | None, evening: dict | None) -> str | None:
    """What the person said on the evening sheet about a block the devices cannot see. Unanswered stays absent."""
    if not evening or tag is None or tag.observable:
        return None
    low = title.lower()
    for key, words in (("workout", WORKOUT_WORDS), ("meditation", MEDITATION_WORDS)):
        if any(w in low for w in words) and evening.get(key) in ("yes", "partly", "no"):
            return evening[key]
    return None


def plan_block(p: Plan, tag: Tag | None, digital: dict, answers: list[dict] | None = None, evening: dict | None = None) -> dict:
    entries, idle = [], 0.0
    for track in ("laptop", "phone"):
        for b in digital[track]:
            ov = _overlap(p.start, p.end, datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            if ov is None:
                continue
            e = _entry(b, *ov)
            if e["category"] == "idle":
                idle += e["minutes"]
            else:
                entries.append(e)
    entries.sort(key=lambda e: (e["start"] if e["start"] >= "05:00" else "~" + e["start"], e["track"]))   # logical-day order
    expected = set(tag.expected) if tag else set()
    on_plan = min(sum(e["minutes"] for e in entries if e["category"] in expected), p.minutes)
    pct = round(100 * on_plan / p.minutes) if p.minutes else 0
    phone = sum(e["minutes"] for e in entries if e["track"] == "phone")
    block = {
        "id": p.id, "title": p.title, "start": _hm(p.start), "end": _hm(p.end), "planned_min": round(p.minutes),
        "source": _source(p), "verdict": _verdict(tag, pct, on_plan, bool(entries) or idle > 0),
        "on_plan_min": round(on_plan), "on_plan_pct": pct, "idle_min": round(idle),
        "phone_min": round(phone), "expected": list(tag.expected) if tag else [],
        "laptop_data_min": round(sum(e["minutes"] for e in entries if e["track"] == "laptop") + idle),
        "activities": entries,
    }
    reasons = [r for r in (_reason(a) for a in answers or [] if a["kind"] == "distraction" and p.start <= a["answered_at"] < p.end) if r]
    if reasons:
        block["reasons"] = reasons
    said = _self_report(p.title, tag, evening)
    if said:
        block["self_report"] = said
    return block


def outside_the_plan(plans: list[Plan], digital: dict, minimum: float = OUTSIDE_MIN) -> list[dict]:
    windows = sorted((p.start, p.end) for p in plans)
    out = []
    for track in ("laptop", "phone"):
        for b in digital[track]:
            if b["category"] == "idle":
                continue
            pieces = [(datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))]
            for w0, w1 in windows:
                nxt = []
                for s, e in pieces:
                    if w1 <= s or w0 >= e:
                        nxt.append((s, e))
                        continue
                    if w0 > s:
                        nxt.append((s, w0))
                    if w1 < e:
                        nxt.append((w1, e))
                pieces = nxt
            for s, e in pieces:
                entry = _entry(b, s, e)
                if entry["minutes"] >= minimum:
                    out.append(entry)
    out.sort(key=lambda e: (e["start"] if e["start"] >= "05:00" else "~" + e["start"], e["track"]))
    return out


def _instant(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(v).astimezone(IST)
    except (TypeError, ValueError):
        return None


def plan_changes(plans: list[Plan], draft: dict | None) -> dict | None:
    """What the user did to the generated plan. None when nothing was generated for the day.

    The draft's `proposed` is the model's original list and `entries` is the same list after the user's edits (title,
    start_at, end_at, state, edited). Times are compared as instants: the text of two ISO strings for the same moment can
    differ (+05:30 against +00:00), which made a naive comparison report a change that never happened."""
    generated = [p for p in plans if p.origin == "generated"]
    if not generated and not draft:
        return None
    out: dict[str, Any] = {
        "kept_unedited": [p.title for p in generated if not p.edited_by_user],
        "edited_after_accepting": [p.title for p in generated if p.edited_by_user],
        "added_by_user": [p.title for p in plans if p.origin != "generated"],
        "discarded": [], "retimed_in_draft": [],
    }
    if draft:
        proposed, entries = draft.get("proposed") or [], draft.get("entries") or []
        out["discarded"] = [e["title"] for e in entries if e.get("state") == "discarded"]
        for old, new in zip(proposed, entries):
            if (_instant(old.get("start_at")), _instant(old.get("end_at"))) != (_instant(new.get("start_at")), _instant(new.get("end_at"))) \
                    or old.get("title") != new.get("title"):
                out["retimed_in_draft"].append(new.get("title") or old.get("title"))
    return out


SINK_CATEGORIES = frozenset({"media", "social", "shopping", "other", "job_search"})
BEST_CATEGORIES = frozenset({"coding", "study"})
TOP_SINKS, TOP_UNMAPPED = 3, 5


def _root(label: str) -> str:
    """'YouTube: some video' and 'YouTube shorts: x' share a root; the part before the first colon."""
    return label.split(":", 1)[0].strip()


def time_sinks(digital: dict, top: int = TOP_SINKS) -> list[dict]:
    """The biggest places the time went, by track and app/site, from the minutes the code measured."""
    minutes: dict[tuple[str, str], float] = {}
    for track in ("laptop", "phone"):
        for b in digital[track]:
            if b["category"] in SINK_CATEGORIES:
                for l in b["labels"]:
                    key = (track, _root(l["label"]))
                    minutes[key] = minutes.get(key, 0.0) + l["minutes"]
    ranked = sorted(minutes.items(), key=lambda kv: -kv[1])[:top]
    return [{"what": f"{track} {root}", "minutes": round(m)} for (track, root), m in ranked]


def unmapped_labels(digital: dict, top: int = TOP_UNMAPPED) -> list[dict]:
    """Labels that landed in `other`: the rules do not know them yet."""
    minutes: dict[str, float] = {}
    for track in ("laptop", "phone"):
        for b in digital[track]:
            if b["category"] == "other":
                for l in b["labels"]:
                    minutes[f"{track}: {l['label']}"] = minutes.get(f"{track}: {l['label']}", 0.0) + l["minutes"]
    return [{"label": k, "minutes": round(m)} for k, m in sorted(minutes.items(), key=lambda kv: -kv[1])[:top] if m >= 10]


def best_stretch(digital: dict, plans: list[Plan]) -> dict | None:
    """The longest block of coding or study, and whether it fell inside a calendar window."""
    best = None
    for track in ("laptop", "phone"):
        for b in digital[track]:
            if b["category"] in BEST_CATEGORIES and (best is None or b["minutes"] > best["minutes"]):
                best = b
    if best is None:
        return None
    s, e = datetime.fromisoformat(best["start"]), datetime.fromisoformat(best["end"])
    return {"track": best["track"], "start": _hm(s), "end": _hm(e), "category": best["category"], "minutes": round(best["minutes"]),
            "label": best["labels"][0]["label"] if best["labels"] else best["category"],
            "inside_plan": any(_overlap(s, e, p.start, p.end) for p in plans)}


def _span_min(b: dict) -> float:
    return (datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() / 60


WORK_CATEGORIES = ("coding", "study", "ai_assist")


def _work_label(label: str) -> str:
    """'VS Code: proj (file.py)' -> 'VS Code: proj'; long titles are cut."""
    return re.sub(r"\s*\([^)]*\)?$", "", label).strip()[:60]


def day_stats(digital: dict) -> dict:
    """Per-day numbers the patterns are computed from: laptop time by clock hour, productive runs, phone co-use."""
    by_hour: dict[str, dict[str, float]] = {}
    productive = []
    for b in digital["laptop"]:
        if b["category"] == "idle":
            continue
        s, e = datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])
        density = b["minutes"] / _span_min(b)
        cur = s
        while cur < e:
            nxt = min((cur.astimezone(IST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)), e)
            part = (nxt - cur).total_seconds() / 60 * density
            h = by_hour.setdefault(f"{cur.astimezone(IST).hour:02d}", {"active": 0.0, "productive": 0.0})
            h["active"] += part
            if b["category"] in PRODUCTIVE:
                h["productive"] += part
            cur = nxt
        if b["category"] in PRODUCTIVE:
            productive.append((s, e, b["minutes"]))
    runs, last_end = [], None
    for s, e, m in sorted(productive):
        if last_end is not None and s == last_end:
            runs[-1] += m
        else:
            runs.append(m)
        last_end = e
    overlap = 0.0
    for pb in digital["phone"]:
        if pb["category"] not in ("social", "media"):
            continue
        ps, pe = datetime.fromisoformat(pb["start"]), datetime.fromisoformat(pb["end"])
        density = pb["minutes"] / _span_min(pb)
        for s, e, _ in productive:
            ov = _overlap(ps, pe, s, e)
            if ov:
                overlap += (ov[1] - ov[0]).total_seconds() / 60 * density
    work: dict[str, float] = {}
    for track in ("laptop", "phone"):
        for b in digital[track]:
            if b["category"] in WORK_CATEGORIES:
                for l in b["labels"]:
                    key = _work_label(l["label"])
                    work[key] = work.get(key, 0.0) + l["minutes"]
    return {
        "work": {k: round(v) for k, v in sorted(work.items(), key=lambda kv: -kv[1])[:8] if v >= 5},
        "by_hour": {h: {k: round(v, 1) for k, v in d.items()} for h, d in sorted(by_hour.items())},
        "productive_runs": [round(r) for r in runs],
        "productive_laptop_min": round(sum(m for _, _, m in productive)),
        "phone_over_productive_min": round(overlap),
    }


def build_practiced_schedule(
    digital: dict, plans: list[Plan], tags: dict[str, Tag], *,
    tasks_done: list[dict] | None = None, tasks_slipping: list[dict] | None = None, draft: dict | None = None,
    answers: list[dict] | None = None, context: str | None = None,
) -> dict:
    plans = sorted(plans, key=lambda p: p.start)
    answers = answers or []
    evening = next((a["payload"] for a in sorted(answers, key=lambda a: a["answered_at"], reverse=True) if a["kind"] == "evening"), None)
    sleep = next((a["payload"] for a in sorted(answers, key=lambda a: a["answered_at"], reverse=True) if a["kind"] == "sleep"), None)
    windows = [(p.start, p.end) for p in plans]
    return {
        "day": digital["day"],
        "context": context or "normal",
        "plan": [plan_block(p, tags.get(p.title), digital, answers, evening) for p in plans],
        "outside_reasons": [r for a in answers if a["kind"] == "distraction" and not any(w0 <= a["answered_at"] < w1 for w0, w1 in windows)
                            for r in [_reason(a)] if r],
        "evening": {k: evening[k] for k in ("workout", "meditation", "naps", "intended_sleep") if evening and k in evening} or None,
        "sleep": ({"start": datetime.fromisoformat(sleep["sleep_start"]).astimezone(IST).strftime("%H:%M"),
                   "wake": datetime.fromisoformat(sleep["wake"]).astimezone(IST).strftime("%H:%M"), "minutes": sleep["minutes"],
                   "edited": bool(sleep.get("edited"))} if sleep and sleep.get("sleep_start") and sleep.get("wake") else None),
        "stats": day_stats(digital),
        "outside": outside_the_plan(plans, digital),
        "sinks": time_sinks(digital),
        "best_stretch": best_stretch(digital, plans),
        "unmapped": unmapped_labels(digital),
        "totals": digital["totals"],
        "tasks": {"done": tasks_done or [], "slipping": tasks_slipping or []},
        "plan_changes": plan_changes(plans, draft),
    }
