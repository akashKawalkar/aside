# schedule_gen/prompt.py — the instructions and the output contract for a schedule draft. The packed context goes in
# the user message; this fixed part goes in the system message and is what the approval card shows first.
from __future__ import annotations

from datetime import date

from llm.client import Message
from schedule_gen.model import Rules

SYSTEM = """You draft one day of a personal schedule for one person. Reply with ONE JSON object and nothing else:
{{"entries": [{{"title": "...", "start": "HH:MM", "end": "HH:MM", "reason": "one short sentence", "task_id": 12}}]}}

Rules:
- Times are 24-hour IST on {day}, between {wake_start} and {wake_end}.
- Propose only blocks worth putting on a calendar: work blocks, workouts, appointments, recurring activities, anything the instructions ask for.
- Do NOT add meals, sleep, commuting, breaks or filler.
- Tasks are listed as "[task N] ...". Put the tasks that should be done on this day into blocks, most urgent first (overdue and postponed ones need a slot soon; a far-off deadline can wait). Choose each block's length from the task itself (a quick errand 15 minutes, a report a couple of hours) and its time from the user's usual rhythm. Set "task_id" to N on such a block, and leave it out on every other block. One block per task. Leave out tasks that can wait.
- Each block is 15 minutes to 6 hours. Never overlap the "fixed" blocks or each other.
- Obey the persistent file's constraints and the instructions. Use the weekday template as a guide to how this day usually looks, and drop or move what the instructions or candidate facts make impossible.
- "reason" says why this block is there (for example "your usual weekday routine" or "you asked for it").
- On a revision, return ONLY blocks that are not fixed. Keep the current draft's blocks exactly as they are unless the instruction requires a change.
- If nothing is worth scheduling, return {{"entries": []}}."""


def system_message(rules: Rules) -> str:
    return SYSTEM.format(day=f"{rules.day:%A %d %B %Y}", wake_start=f"{rules.waking_start:%H:%M}", wake_end=f"{rules.waking_end:%H:%M}")


def build_messages(context: str, rules: Rules, instruction: str = "", revising: bool = False) -> list[Message]:
    ask = f"{'Revise' if revising else 'Draft'} the schedule for {rules.day:%A %d %B %Y}."
    if instruction.strip():
        ask += f"\nInstruction: {instruction.strip()}"
    return [Message("system", system_message(rules)), Message("user", f"{context}\n\n{ask}" if context else ask)]
