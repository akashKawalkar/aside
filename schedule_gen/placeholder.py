# schedule_gen/placeholder.py — the rule-based generator: copy what the user's last same weekday looked like.
# No model call, no quota. It is the fallback when the daily cap is spent and the BASELINE the LLM draft is measured
# against, so it deliberately does nothing clever: no task blocks, no meal/sleep filler, no invented entries.
from __future__ import annotations

from datetime import datetime
from typing import Any

from schedule_gen.gather import GenInputs
from schedule_gen.model import IST, DraftEntry
from schedule_gen.validator import validate


def generate(inputs: GenInputs) -> tuple[list[DraftEntry], list[dict[str, Any]]]:
    """(entries, rejected). A copied block that no longer fits (it overlaps something fixed, or falls outside the waking
    window) is rejected with the validator's reason rather than silently moved."""
    day = inputs.rules.day
    when = f"Copied from {inputs.template_day:%a %d %b}" if inputs.template_day else "Copied"
    copied = []
    for t in inputs.template:
        start = datetime.combine(day, t["start_at"].astimezone(IST).time(), IST)
        copied.append(DraftEntry(title=t["title"], start_at=start, end_at=start + (t["end_at"] - t["start_at"]), reason=when))
    return validate(copied, inputs.rules, inputs.fixed)
