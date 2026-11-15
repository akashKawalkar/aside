"""The digital schedule: what the devices show happened on one day, as time blocks. Pure code, no database, no model.

Steps (planning_design.md section 2.1):
 1. events of one 05:00-to-05:00 day (IST), clipped to that window;
 2. laptop: idle events cut app time and become idle fragments; a Chrome fragment takes its domain and title from the
    browser event it overlaps most; the phone only has package names;
 3. every fragment gets a category and label from the rules; `system` fragments are dropped;
 4. 10-minute buckets, majority category per bucket, buckets with under 2 minutes of data skipped, adjacent buckets of the
    same category merged into one block;
 5. laptop and phone are separate tracks (they overlap in real time);
 6. each block keeps its top labels with minutes in the order they first appeared;
 7. a block that starts between 23:00 and 06:00 is flagged `late_night`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from digital.rules import Rule, classify, ordered

IST = ZoneInfo("Asia/Kolkata")
BUCKET = timedelta(minutes=10)
MIN_BUCKET_SECONDS = 120
MAX_LABELS = 8
BROWSER_APPS = ("chrome.exe",)


@dataclass(frozen=True)
class Ev:
    """One row of the `events` table, with only what the builder needs."""
    source: str                 # laptop | browser | android
    kind: str                   # app_focus | idle | phone_app_focus
    start: datetime
    end: datetime
    app: str | None = None
    domain: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class Frag:
    track: str                  # laptop | phone
    start: datetime
    end: datetime
    category: str
    label: str


def day_window(day: date) -> tuple[datetime, datetime]:
    s = datetime.combine(day, time(5, 0), IST)
    return s, s + timedelta(days=1)


def _clip(start: datetime, end: datetime, lo: datetime, hi: datetime):
    s, e = max(start, lo), min(end, hi)
    return (s, e) if e > s else None


def _subtract(frag: tuple[datetime, datetime], cuts: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """`frag` minus every interval in `cuts` (cuts sorted by start)."""
    pieces = [frag]
    for cs, ce in cuts:
        nxt = []
        for s, e in pieces:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        pieces = nxt
        if not pieces:
            break
    return pieces


def _best_browser_event(start: datetime, end: datetime, browser: list[Ev]) -> Ev | None:
    best, best_overlap = None, timedelta(0)
    for b in browser:
        if b.start >= end:
            break
        o = min(end, b.end) - max(start, b.start)
        if o > best_overlap:
            best, best_overlap = b, o
    return best


def fragments(events: list[Ev], rules: list[Rule], lo: datetime, hi: datetime) -> list[Frag]:
    rules = ordered(rules)
    ev = sorted((e for e in events if e.end > lo and e.start < hi), key=lambda e: e.start)
    browser = [e for e in ev if e.source == "browser"]
    idle = [(max(e.start, lo), min(e.end, hi)) for e in ev if e.source == "laptop" and e.kind == "idle"]
    idle.sort()
    out: list[Frag] = []

    for e in ev:
        if e.source == "laptop" and e.kind == "app_focus":
            clipped = _clip(e.start, e.end, lo, hi)
            if not clipped:
                continue
            domain, title = None, e.title
            if (e.app or "").lower() in BROWSER_APPS:
                b = _best_browser_event(clipped[0], clipped[1], browser)
                if b is not None:
                    domain, title = b.domain, b.title or e.title
            m = classify(rules, "laptop", e.app, domain, title)
            if m.category == "system":
                continue
            for s, t in _subtract(clipped, idle):
                out.append(Frag("laptop", s, t, m.category, m.label))
        elif e.source == "android":
            clipped = _clip(e.start, e.end, lo, hi)
            if not clipped:
                continue
            m = classify(rules, "phone", e.app, None, None)
            if m.category != "system":
                out.append(Frag("phone", clipped[0], clipped[1], m.category, m.label))

    for s, t in idle:
        if t > s:
            out.append(Frag("laptop", s, t, "idle", "idle"))
    out.sort(key=lambda f: (f.start, f.end))
    return out


def _bucket_index(t: datetime, lo: datetime) -> int:
    return int((t - lo) / BUCKET)


def blocks(frags: list[Frag], track: str, lo: datetime, hi: datetime) -> list[dict]:
    mine = [f for f in frags if f.track == track]
    seconds: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for f in mine:
        i = _bucket_index(f.start, lo)
        while True:
            b_start = lo + i * BUCKET
            if b_start >= f.end:
                break
            part = _clip(f.start, f.end, b_start, b_start + BUCKET)
            if part:
                seconds[i][f.category] += (part[1] - part[0]).total_seconds()
            i += 1

    runs: list[tuple[str, int, int]] = []          # category, first bucket, last bucket
    for i in sorted(seconds):
        cats = seconds[i]
        if sum(cats.values()) < MIN_BUCKET_SECONDS:
            continue
        top = max(cats, key=lambda c: (cats[c], c))
        if runs and runs[-1][0] == top and runs[-1][2] == i - 1:
            runs[-1] = (top, runs[-1][1], i)
        else:
            runs.append((top, i, i))

    result = []
    for cat, first, last in runs:
        start = lo + first * BUCKET
        end = min(lo + (last + 1) * BUCKET, hi)
        label_secs: dict[str, float] = {}
        order: list[str] = []
        total = 0.0
        for f in mine:
            if f.category != cat:
                continue
            part = _clip(f.start, f.end, start, end)
            if not part:
                continue
            secs = (part[1] - part[0]).total_seconds()
            total += secs
            if f.label not in label_secs:
                order.append(f.label)
                label_secs[f.label] = 0.0
            label_secs[f.label] += secs
        keep = set(sorted(order, key=lambda l: -label_secs[l])[:MAX_LABELS])
        labels = [{"label": l, "minutes": round(label_secs[l] / 60, 1)} for l in order if l in keep]
        local = start.astimezone(IST)
        result.append({
            "track": track,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "category": cat,
            "minutes": round(total / 60, 1),
            "labels": labels,
            "late_night": local.hour >= 23 or local.hour < 6,
        })
    return result


def totals(frags: list[Frag]) -> dict[str, dict[str, float]]:
    t: dict[str, dict[str, float]] = {"laptop": defaultdict(float), "phone": defaultdict(float)}
    for f in frags:
        t[f.track][f.category] += (f.end - f.start).total_seconds() / 60
    return {k: {c: round(m, 1) for c, m in sorted(v.items(), key=lambda kv: -kv[1])} for k, v in t.items()}


def build_digital_schedule(day: date, events: list[Ev], rules: list[Rule]) -> dict:
    lo, hi = day_window(day)
    frags = fragments(events, rules, lo, hi)
    return {
        "day": day.isoformat(),
        "window": [lo.isoformat(), hi.isoformat()],
        "laptop": blocks(frags, "laptop", lo, hi),
        "phone": blocks(frags, "phone", lo, hi),
        "totals": totals(frags),
    }


def unmapped(day: date, events: list[Ev], rules: list[Rule], top: int = 15) -> list[tuple[str, float]]:
    """The apps and sites no rule knows, by minutes, so the user can map the biggest ones."""
    lo, hi = day_window(day)
    rs = ordered(rules)
    secs: dict[str, float] = defaultdict(float)
    browser = sorted((e for e in events if e.source == "browser"), key=lambda e: e.start)
    for e in events:
        if e.kind not in ("app_focus", "phone_app_focus"):
            continue
        c = _clip(e.start, e.end, lo, hi)
        if not c or e.source == "browser":
            continue
        source = "phone" if e.source == "android" else "laptop"
        domain, title = None, e.title
        if source == "laptop" and (e.app or "").lower() in BROWSER_APPS:
            b = _best_browser_event(c[0], c[1], browser)
            if b is not None:
                domain, title = b.domain, b.title or e.title
        m = classify(rs, source, e.app, domain, title)
        if m.rule_id == 0 and m.category == "other":
            secs[f"{source}: {domain or e.app}"] += (c[1] - c[0]).total_seconds()
    return sorted(((k, round(v / 60, 1)) for k, v in secs.items()), key=lambda kv: -kv[1])[:top]
