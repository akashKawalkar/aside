# context/sources/schedule_gen.py — the sources only the `schedule` recipe uses. All of them read one pre-gathered
# GenInputs (schedule_gen/gather.py) from `situation.extra["gen_inputs"]`, so a compile makes no further queries and
# the model sees exactly what the placeholder generator sees. Plain text only; render() owns the formatting.
from __future__ import annotations

from typing import Any

from context.items import Item, Situation
from schedule_gen.model import IST, hhmm, span


def _inputs(situation: Situation):
    return situation.extra.get("gen_inputs")


class _GenSource:
    name = ""

    async def fetch(self, situation: Situation) -> list[Item]:
        inputs = _inputs(situation)
        return [] if inputs is None else self.items(inputs)

    def items(self, inputs) -> list[Item]:
        raise NotImplementedError


class FixedSource(_GenSource):
    """Blocks already on the day: nothing may overlap them."""
    name = "fixed"

    def items(self, inputs):
        blocks = sorted(inputs.fixed, key=lambda b: b.start_at)
        return [Item(id=f"fixed:{n}", text=f"{span(b.start_at, b.end_at)} {b.title}".strip(), source=self.name, priority=-n)
                for n, b in enumerate(blocks)]


class CurrentDraftSource(_GenSource):
    """On a revision: the open draft's entries that are not locked. Kept unless the instruction needs a change."""
    name = "current_draft"

    def items(self, inputs):
        return [Item(id=f"draft:{n}", text=f"{span(e.start_at, e.end_at)} {e.title}", source=self.name, priority=-n)
                for n, e in enumerate(sorted(inputs.current, key=lambda e: e.start_at))]


class WeekdayTemplateSource(_GenSource):
    """What the user's last same weekday looked like (their end-of-day snapshot, user-made or user-edited entries)."""
    name = "weekday_template"

    def items(self, inputs):
        if not inputs.template_day:
            return []
        group = f"{inputs.template_day:%A %d %b}"
        return [Item(id=f"template:{n}", text=f"{span(t['start_at'], t['end_at'])} {t['title']}", source=self.name, priority=-n, group=group)
                for n, t in enumerate(sorted(inputs.template, key=lambda t: t["start_at"]))]


class InstructionsSource(_GenSource):
    """What the user asked for. Core: always in, because it is the point of the request."""
    name = "instructions"

    def items(self, inputs):
        return [Item(id=f"instruction:{n}", text=text, source=self.name, priority=-n, core=True, provenance="user")
                for n, text in enumerate(inputs.instructions)]


class CandidatesSource(_GenSource):
    """Candidate items valid for the day (extracted facts like "travelling", "tomorrow is ekadashi")."""
    name = "candidates"

    def items(self, inputs):
        out = []
        for n, c in enumerate(inputs.candidates):
            effect = f" -> {c['effect']}" if c.get("effect") else ""
            out.append(Item(id=f"candidate:{c['id']}", text=f"{c['text']}{effect}", source=self.name, priority=-n,
                            confidence=c["confidence"], provenance=f"candidate:{c['id']}"))
        return out


class ScheduleTasksSource(_GenSource):
    """Pending tasks due by the end of the day, overdue ones included. The model time-blocks them and echoes the id."""
    name = "tasks"

    def items(self, inputs):
        out = []
        for n, t in enumerate(inputs.tasks):
            due = t["due_at"].astimezone(IST)
            notes = [f"due {due:%a %d %b %H:%M}"]
            overdue = (inputs.rules.day - due.date()).days
            if overdue > 0:
                notes.append(f"overdue by {overdue} day{'s' if overdue != 1 else ''}")
            if t.get("slip_count"):
                notes.append(f"postponed {t['slip_count']}x")
            out.append(Item(id=f"task:{t['id']}", text=f"[task {t['id']}] {t['text']} ({'; '.join(notes)})", source=self.name, priority=-n,
                            provenance=f"task:{t['id']}"))
        return out


class FreeSlotsSource(_GenSource):
    """The gaps a new block could go in: one line, so the model does not have to do the arithmetic."""
    name = "free_slots"

    def items(self, inputs):
        if not inputs.free:
            return [Item(id="free:none", text="no free time left in the waking window", source=self.name)]
        return [Item(id="free:slots", text=", ".join(f"{hhmm(a)}-{hhmm(b)}" for a, b in inputs.free), source=self.name)]


def schedule_gen_sources(persistent_file: Any, empty: Any, observations: Any | None = None) -> dict[str, Any]:
    """Every source the `schedule` recipe names."""
    return {
        "persistent_file": persistent_file,
        "instructions": InstructionsSource(),
        "fixed": FixedSource(),
        "current_draft": CurrentDraftSource(),
        "candidates": CandidatesSource(),
        "weekday_template": WeekdayTemplateSource(),
        "tasks": ScheduleTasksSource(),
        "free_slots": FreeSlotsSource(),
        "observations": observations or empty("observations"),
        "history": empty("history"),
    }
