# schedule_gen/service.py — everything the routes and the nightly job do with a draft, in one place. Plan 3.6, steps 1-7.
#
#   status        can the user ask for a draft of tomorrow, and is one open?
#   stage         gather -> compile the `schedule` recipe -> stage ONE model call for the user's approval
#   finalize      the approved reply -> parse -> validate -> a new open draft version
#   placeholder   the same draft without a model call (rule-based; also the baseline)
#   edit / accept / discard   what the user does with the entries
#
# Nothing here writes to the schedule except accept, and accept goes through storage.accept_entries (one transaction).
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

import storage
from context.compile import SaveLog, compile_context
from context.items import Situation
from context.recipe import Recipe
from context.sources import EmptySource
from context.sources.persistent_file import persistent_file_source
from context.sources.schedule_gen import schedule_gen_sources
from llm.client import Completion, ModelProfile
from schedule_gen import placeholder
from schedule_gen.gather import gather
from schedule_gen.model import ACCEPTED, DISCARDED, IST, PENDING, Block, DraftEntry, Rules, tomorrow
from schedule_gen.parse import parse_entries
from schedule_gen.prompt import build_messages
from schedule_gen.validator import check, validate

KIND = "schedule"                      # the trace operation of a schedule call
GEN_MAX_TOKENS = 3000               # reasoning tokens count inside this: a small cap can leave no room for the answer (measured)
GEN_TEMPERATURE = 0.3


class DraftError(ValueError):
    """A refusal the user should be told about in plain words (nothing is wrong with the system)."""


def draft_to_api(draft: dict[str, Any]) -> dict[str, Any]:
    out = dict(draft)
    out["target_day"] = draft["target_day"].isoformat()
    for key in ("created_at", "resolved_at"):
        out[key] = draft[key].isoformat() if draft[key] else None
    out["entries"] = [{**e, "index": i} for i, e in enumerate(draft["entries"])]
    return out


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, IST)
    return start, start + timedelta(days=1)


# ---------- can the user ask? ----------

async def status(pool, now: datetime, day: date | None = None) -> dict[str, Any]:
    """The button is off if tomorrow already has anything on it, unless a draft is open (then the draft is shown instead).
    `day` defaults to tomorrow; the morning retry of the nightly job asks about today."""
    day = day or tomorrow(now)
    draft = await storage.get_open_draft(pool, day)
    start, end = _day_bounds(day)
    has_entries = bool(await storage.list_schedule_range(pool, start=start, end=end))
    reason = "draft_open" if draft else ("tomorrow_has_entries" if has_entries else None)
    return {"day": day.isoformat(), "can_generate": reason is None, "reason": reason,
            "draft": draft_to_api(draft) if draft else None}


async def _require_can_generate(pool, now: datetime) -> date:
    state = await status(pool, now)
    if state["reason"] == "draft_open":
        raise DraftError("There is already an open draft for tomorrow. Revise it, or discard it first.")
    if state["reason"] == "tomorrow_has_entries":
        raise DraftError("Tomorrow already has entries, so there is nothing to generate.")
    return date.fromisoformat(state["day"])


# ---------- step 1-4: gather, compile, stage ----------

async def prepare_generation(
    *, pool, profile: ModelProfile, recipe: Recipe, rules: Rules, instruction: str, save_log: SaveLog | None,
    max_item_tokens: int | None, revising: dict[str, Any] | None = None,
):
    """Gather and compile the context for the day. Returns (messages, compiled, inputs). Sends nothing.
    The instruction (if any) is saved as a one-shot instruction record scoped to the day, and kept for the pattern finder."""
    instruction = (instruction or "").strip()
    if instruction:
        await storage.insert_instruction_record(pool, instruction, valid_from=rules.day, valid_until=rules.day)
        await storage.insert_statement(pool, instruction, "instruction")

    inputs = await gather(pool, rules.day, rules, open_draft=revising)
    from context.sources.observations import observations_source
    sources = schedule_gen_sources(persistent_file_source(pool), EmptySource, observations_source(pool))
    compiled = await compile_context(
        Situation("schedule", query=instruction, extra={"gen_inputs": inputs}), recipe, sources, profile,
        save_log=save_log, max_item_tokens=max_item_tokens,
    )
    messages = build_messages(compiled.text, rules, instruction, revising=revising is not None)
    return messages, compiled, inputs


def _carry_over(previous: dict[str, Any] | None) -> list[DraftEntry]:
    """What a revision keeps verbatim: entries already accepted (they are schedule rows now) and pending entries the
    user edited. Everything else in the previous draft is the model's to redo."""
    if not previous:
        return []
    entries = [DraftEntry.from_json(e) for e in previous["entries"]]
    return [e for e in entries if e.state == ACCEPTED or (e.state == PENDING and e.locked)]


# ---------- parse, validate, store ----------

async def finalize_generation(pool, call, completion: Completion, rules: Rules) -> dict[str, Any]:
    """Raises schedule_gen.parse.ParseError if the reply is unusable; the call is spent either way, and the route says so.
    Entries the validator rejects are kept on the draft (`rejected`) with their reason, never dropped silently."""
    entries, unreadable = parse_entries(completion.text, rules.day)

    previous = await storage.get_open_draft(pool, rules.day)          # re-read: the user may have edited since staging
    inputs = await gather(pool, rules.day, rules, open_draft=previous)
    valid, rejected = validate(entries, rules, inputs.fixed, {t["id"] for t in inputs.tasks})

    merged = sorted(_carry_over(previous) + valid, key=lambda e: (e.start_at, e.end_at))
    draft = await storage.insert_draft(
        pool, target_day=rules.day, source="llm", model=completion.model, instruction=call.meta.get("instruction", ""),
        entries=[e.to_json() for e in merged], rejected=unreadable + rejected, inputs=call.meta.get("inputs", inputs.summary()),
        compile_log_id=call.compile_log_id,
    )
    return {"draft": draft_to_api(draft)}


# ---------- drafting with a model ----------

class GenerationIncomplete(RuntimeError):
    """The model's reply was cut off (finish_reason "length"): never treat it as a normal answer."""


@dataclass
class CallInfo:
    """What finalize_generation needs to know about the call that produced the reply."""
    meta: dict[str, Any]
    compile_log_id: int | None = None


Complete = Callable[[list, int | None], Awaitable[Completion]]     # (messages, compile_log_id) -> the model's reply


async def draft_with_model(
    pool, complete: Complete, *, rules: Rules, profile: ModelProfile, recipe: Recipe, save_log: SaveLog | None,
    max_item_tokens: int | None, instruction: str = "", revising: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Completion]:
    """Gather, compile, ask the model (`complete` decides the grant: user or background), then parse, validate and store the
    reply as a new open draft. Returns (draft as the API shows it, the completion). Raises GenerationIncomplete if the reply
    was cut off, schedule_gen.parse.ParseError if it is unusable (the call is spent either way)."""
    messages, compiled, inputs = await prepare_generation(
        pool=pool, profile=profile, recipe=recipe, rules=rules, instruction=instruction, save_log=save_log,
        max_item_tokens=max_item_tokens, revising=revising,
    )
    completion = await complete(messages, compiled.id)
    if completion.finish_reason == "length":
        raise GenerationIncomplete("the model's reply was cut off before the schedule was complete")
    call = CallInfo(meta={"day": rules.day.isoformat(), "instruction": instruction, "inputs": inputs.summary(),
                          "revises": revising["id"] if revising else None}, compile_log_id=compiled.id)
    return (await finalize_generation(pool, call, completion, rules))["draft"], completion


async def generate_unattended(
    pool, client, *, now: datetime, rules: Rules, profile: ModelProfile, recipe: Recipe, save_log: SaveLog | None,
    max_item_tokens: int | None, instruction: str = "",
) -> dict[str, Any]:
    """Draft `rules.day` with no one watching and accept the whole draft as `generated` schedule rows.
    `client` is a gated client (llm.factory.create_client); the call goes out inside a background grant, so it is subject to
    [llm] background_enabled and the daily cap. Nothing is attempted if the day already has entries or an open draft, which
    also makes a rerun (or the morning retry after a good night) a no-op. Raises on any failure: the caller owns retries."""
    from llm.approved import Grant, approved

    state = await status(pool, now, rules.day)
    if state["reason"]:
        return {"skipped": state["reason"], "day": rules.day.isoformat()}

    async def complete(messages, compile_log_id):
        with approved(Grant("background", operation=KIND, compile_log_id=compile_log_id)):
            return await client.complete(
                messages, response_format={"type": "json_object"}, max_tokens=GEN_MAX_TOKENS, temperature=GEN_TEMPERATURE,
            )

    draft, completion = await draft_with_model(
        pool, complete, rules=rules, profile=profile, recipe=recipe, save_log=save_log, max_item_tokens=max_item_tokens,
        instruction=instruction,
    )
    accepted = await accept(pool, draft["id"], None)
    return {"day": rules.day.isoformat(), "draft_id": draft["id"], "model": completion.model, "proposed": len(draft["entries"]),
            "accepted": len(accepted["accepted"]), "conflicts": len(accepted["conflicts"]), "rejected": len(draft["rejected"])}


# ---------- the rule-based draft ----------

async def create_placeholder_draft(pool, now: datetime, rules: Rules) -> dict[str, Any]:
    """The same kind of draft with no model call and no quota: last same weekday's blocks, minus whatever no longer fits."""
    await _require_can_generate(pool, now)
    inputs = await gather(pool, rules.day, rules)
    entries, rejected = placeholder.generate(inputs)
    draft = await storage.insert_draft(
        pool, target_day=rules.day, source="placeholder", model=None, instruction="",
        entries=[e.to_json() for e in entries], rejected=rejected, inputs=inputs.summary(),
    )
    return draft_to_api(draft)


# ---------- what the user does with entries ----------

async def _open_draft(pool, draft_id: int) -> dict[str, Any]:
    draft = await storage.get_draft(pool, draft_id)
    if draft is None:
        raise DraftError("No such draft.")
    if draft["status"] != "open":
        raise DraftError(f"That draft is already {draft['status']}.")
    return draft


@dataclass
class EntryEdit:
    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None


async def edit_entry(pool, draft_id: int, index: int, edit: EntryEdit, rules: Rules) -> dict[str, Any]:
    """Change a pending entry inside the draft. It is checked like any proposal (waking hours, no overlaps) and becomes
    locked, so a later revision leaves it alone."""
    draft = await _open_draft(pool, draft_id)
    entries = [DraftEntry.from_json(e) for e in draft["entries"]]
    if not 0 <= index < len(entries):
        raise DraftError("No such entry.")
    target = entries[index]
    if target.state != PENDING:
        raise DraftError(f"That entry is already {target.state}.")

    changed = DraftEntry(
        title=edit.title if edit.title is not None else target.title,
        start_at=(edit.start_at or target.start_at).astimezone(IST), end_at=(edit.end_at or target.end_at).astimezone(IST),
        reason=target.reason, state=PENDING, locked=True, edited=True, task_id=target.task_id,
    )

    start, end = _day_bounds(rules.day)
    fixed = [Block(r["start_at"].astimezone(IST), r["end_at"].astimezone(IST), r["title"])
             for r in await storage.list_schedule_range(pool, start=start, end=end)]
    fixed += [Block(e.start_at, e.end_at, e.title) for i, e in enumerate(entries) if i != index and e.state == PENDING]
    problem = check(changed, rules, fixed)
    if problem:
        raise DraftError(_explain(problem))

    entries[index] = changed
    saved = await storage.save_entries(pool, draft_id, [e.to_json() for e in entries])
    return draft_to_api(saved)


def _explain(reason: str) -> str:
    words = {
        "empty_title": "The title is empty.", "title_too_long": "The title is too long.", "bad_times": "The end must be after the start.",
        "wrong_day": "That is not on the day being planned.", "outside_waking_hours": "That falls outside waking hours.",
        "too_short": "That block is too short.", "too_long": "That block is too long.",
    }
    if reason.startswith("overlaps_fixed:"):
        return f"That overlaps {reason.split(':', 1)[1]}."
    return words.get(reason, reason)


async def accept(pool, draft_id: int, indexes: list[int] | None) -> dict[str, Any]:
    """Accept the chosen pending entries (all of them when `indexes` is None). Accepted entries become schedule rows
    tagged `generated`; any that now clash with something added since stay pending and are reported."""
    draft = await _open_draft(pool, draft_id)
    chosen = indexes if indexes is not None else [i for i, e in enumerate(draft["entries"]) if e["state"] == PENDING]
    result = await storage.accept_entries(pool, draft_id, chosen)
    if result is None:
        raise DraftError("That draft is no longer open.")
    return {"draft": draft_to_api(result["draft"]), "accepted": result["accepted"], "conflicts": result["conflicts"]}


async def discard(pool, draft_id: int, indexes: list[int] | None) -> dict[str, Any]:
    """Discard some pending entries, or the whole draft when `indexes` is None. Nothing is ever written to the schedule."""
    draft = await _open_draft(pool, draft_id)
    if indexes is None:
        closed = await storage.discard_draft(pool, draft_id)
        return draft_to_api(closed)
    entries = [DraftEntry.from_json(e) for e in draft["entries"]]
    for i in indexes:
        if 0 <= i < len(entries) and entries[i].state == PENDING:
            entries[i].state = DISCARDED
    saved = await storage.save_entries(pool, draft_id, [e.to_json() for e in entries])
    return draft_to_api(saved)
