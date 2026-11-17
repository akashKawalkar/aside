"""The daily digest: one model call over a day's practiced schedule (planning_design.md 2.4).

Code decides every fact and number: verdicts, percentages, minutes, the three time sinks, the best stretch. The model only
writes the interpretation next to them, in a fixed JSON shape. Output is checked; a bad answer is re-asked up to twice with
the problems listed, and after that the call raises (no template fallback).
"""
from __future__ import annotations

import json
from typing import Any

from llm.client import LLMClient, Message
from practiced.gold import GOLD_DAY, GOLD_OUTPUT
from practiced.llmcall import BadReply, call_json
from practiced.tags import ALLOWED

MAX_TOKENS = 3500            # hidden reasoning tokens count inside this
RETRIES = 2
LABELS_SHOWN = 5
LABEL_CHARS = 70


class DigestError(RuntimeError):
    """The model could not produce a valid digest after the retries."""


# --- what the model reads ---------------------------------------------------------------------------------------------

def _labels(labels: list[dict]) -> str:
    return " -> ".join(f"{l['label'][:LABEL_CHARS]} {l['minutes']:g}m" for l in labels[:LABELS_SHOWN])


def _line(a: dict) -> str:
    late = " LATE-NIGHT" if a.get("late_night") else ""
    return f"{a['track']} {a['start']}-{a['end']} {a['category']} {a['minutes']:g}m{late}: {_labels(a['labels'])}"


def _reason(r: dict) -> str:
    bits = [r["reason"]] + [f"{k} {r[k]}" for k in ("minutes", "apps") if r.get(k)] + ([f'"{r["text"]}"'] if r.get("text") else [])
    return ", ".join(str(b) for b in bits)


TASK_CHECK = {"already_done": "already done", "starting_now": "starting now", "blocked": "blocked / needs info",
              "not_today": "not today", "note": "left a note"}
DEBRIEF = {"continue_tomorrow": "continuing tomorrow", "finished": "finished", "paused": "paused / shelved"}


def _said(a: dict, key: str, words: dict) -> str:
    """A task check or debrief answer as a short phrase, with the person's own words quoted."""
    v = a.get(key)
    return words.get(v, str(v or "no choice")) + (f' "{a["text"]}"' if a.get("text") else "")


def render(p: dict) -> str:
    """The practiced schedule as plain text, time-ordered, the way the design sketches it."""
    out = [f"DAY {p['day']}" + (f" (the person said this day was: {p['context']})" if p.get("context", "normal") != "normal" else ""),
           "", "PLAN BLOCKS (final calendar, in order)"]
    for i, b in enumerate(p["plan"], 1):
        out.append(f'{i}. {b["start"]}-{b["end"]} "{b["title"]}" [{b["source"]}] VERDICT {b["verdict"]}'
                   + (f' on-plan {b["on_plan_min"]}m of {b["planned_min"]}m ({b["on_plan_pct"]}%)' if b["verdict"] not in ("NO SIGNAL", "UNTAGGED") else "")
                   + f' | idle {b["idle_min"]}m | phone in window {b["phone_min"]}m'
                   + (f' | the person said: {b["self_report"]}' if b.get("self_report") else ""))
        out += [f"     {_line(a)}" for a in b["activities"]]
        out += [f"     the person gave a reason at {r['at']}: {_reason(r)}" for r in b.get("reasons", [])]
        if b.get("task_check"):
            out.append(f"     the person said just before it (task check): {_said(b['task_check'], 'status', TASK_CHECK)}")
        if b.get("debrief"):
            out.append(f"     the person said after it (debrief): {_said(b['debrief'], 'outcome', DEBRIEF)}")
    if not p["plan"]:
        out.append("(no calendar blocks this day)")
    out += ["", "OUTSIDE THE PLAN (30m or more, not idle)"]
    out += [f"   {_line(a)}" for a in p["outside"]] or ["   (none)"]
    out += ["", "TIME SINKS (measured by code, biggest first)"]
    out += [f"{i}. {s['what']}: {s['minutes']}m" for i, s in enumerate(p["sinks"], 1)] or ["(none)"]
    bs = p["best_stretch"]
    out += ["", "BEST STRETCH (measured by code): " + (
        f"{bs['start']}-{bs['end']} {bs['track']} {bs['category']} {bs['minutes']}m, {bs['label'][:LABEL_CHARS]}, "
        f"{'inside' if bs['inside_plan'] else 'outside'} the plan" if bs else "none")]
    if p.get("outside_reasons"):
        out += ["", "REASONS THE PERSON GAVE OUTSIDE THE PLAN"] + [f"   {r['at']}: {_reason(r)}" for r in p["outside_reasons"]]
    ev, sl = p.get("evening"), p.get("sleep")
    if ev:
        out += ["", "EVENING SHEET (the person's own answers): " + json.dumps(ev)]
    if sl:
        who = "inferred from the longest stretch with no input on either device" if sl.get("inferred") else "the person's answer"
        out += [f"SLEEP ({who}): {sl['start']} to {sl['wake']}, {sl['minutes']}m"]
    out += ["", "TOTALS PER TRACK (minutes): " + json.dumps(p["totals"])]
    if p["unmapped"]:
        out += ["", "UNMAPPED (no rule knows these): " + "; ".join(f"{u['label']} {u['minutes']}m" for u in p["unmapped"])]
    c = p["plan_changes"]
    if c:
        out += ["", "WHAT HAPPENED TO THE GENERATED PLAN: " + json.dumps({k: v for k, v in c.items() if v})]
    t = p["tasks"]
    if t["done"] or t["slipping"]:
        out += ["", "TASKS: " + json.dumps(t)]
    return "\n".join(out)


SYSTEM = f"""You write the daily digest of how a person's day really went, from a plan-versus-device-activity report.
Code has already measured everything. Verdicts, percentages and minutes in the report are final: never recompute, round,
re-add or contradict them. You only write the short interpretation next to them.

Rules:
- Use only what the report shows. Never claim what the devices cannot see: a workout, a meditation, a meal or a walk
  has no signal, so say so instead of saying it was skipped or done.
- When the data is ambiguous (for example LinkedIn search could be job search or lead-finding), say it is unclear.
- Be specific: name the apps, sites and times that explain a verdict. One or two sentences per note.
- Lines that say "the person said" or "gave a reason" are the person's own answers: you may rely on them and quote them briefly.
  A self-report does not change the VERDICT.
- Do not give advice and do not judge the person.
- Each note must agree with its block's VERDICT. Do not state any total that is not in the report.
- UNMAPPED: for each listed label you can classify with confidence, add it to title_tags with one category from:
  {", ".join(ALLOWED)}. Skip a label when you are not sure. Only use the labels exactly as listed.

Answer with one JSON object, in this order, nothing else:
{{"plan_blocks": [{{"title": "<exact title, same order as the report>", "note": "..."}}],
 "time_sinks": [{{"what": "<exact name from TIME SINKS, same order>", "note": "..."}}],
 "best_stretch": "<one short sentence about the BEST STRETCH, or null if none>",
 "anomalies": ["<at most 4 short things that stand out: late-night use, plan ignored, plan edited, a task that keeps slipping>"],
 "summary": "<two or three sentences on the whole day>",
 "title_tags": [{{"label": "<exact UNMAPPED label>", "category": "<category>"}}]}}
One plan_blocks entry per plan block and one time_sinks entry per time sink. Write the notes first and the summary last.

EXAMPLE REPORT
{render(GOLD_DAY)}

EXAMPLE ANSWER
{json.dumps(GOLD_OUTPUT, ensure_ascii=False)}"""


# --- checking and assembling -----------------------------------------------------------------------------------------

def validate(out: Any, p: dict) -> list[str]:
    """What is wrong with the model's answer, as sentences it can act on. Empty means it is usable."""
    if not isinstance(out, dict):
        return ["The answer must be one JSON object."]
    bad: list[str] = []

    blocks = out.get("plan_blocks")
    want = [b["title"] for b in p["plan"]]
    if not isinstance(blocks, list) or len(blocks) != len(want):
        bad.append(f"plan_blocks must be a list with exactly {len(want)} entries, one per plan block, in the report's order.")
    else:
        for i, (b, title) in enumerate(zip(blocks, want), 1):
            if not isinstance(b, dict) or b.get("title") != title:
                bad.append(f'plan_blocks entry {i} must have title "{title}" exactly.')
            elif not isinstance(b.get("note"), str) or not b["note"].strip() or len(b["note"]) > 400:
                bad.append(f'plan_blocks entry {i} needs a "note" of 1 to 400 characters.')

    sinks = out.get("time_sinks")
    if not isinstance(sinks, list) or len(sinks) != len(p["sinks"]):
        bad.append(f"time_sinks must be a list with exactly {len(p['sinks'])} entries, in the report's order.")
    else:
        for i, (s, want_s) in enumerate(zip(sinks, p["sinks"]), 1):
            if not isinstance(s, dict) or s.get("what") != want_s["what"]:
                bad.append(f'time_sinks entry {i} must have what "{want_s["what"]}" exactly.')
            elif not isinstance(s.get("note"), str) or not s["note"].strip() or len(s["note"]) > 300:
                bad.append(f'time_sinks entry {i} needs a "note" of 1 to 300 characters.')

    bs = out.get("best_stretch")
    if bs is not None and (not isinstance(bs, str) or not bs.strip()):
        bad.append('best_stretch must be a sentence or null.')
    if bs and not p["best_stretch"]:
        bad.append("best_stretch must be null: the report has no best stretch.")

    an = out.get("anomalies")
    if not isinstance(an, list) or len(an) > 4 or not all(isinstance(x, str) and x.strip() for x in an):
        bad.append("anomalies must be a list of at most 4 non-empty strings.")

    summary = out.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 700:
        bad.append("summary must be a string of 1 to 700 characters.")
    return bad


def assemble(out: dict, p: dict) -> dict:
    """The stored digest: the model's words, with every verdict and number put in by code."""
    plan_blocks = []
    for b, said in zip(p["plan"], out["plan_blocks"]):
        row: dict[str, Any] = {"title": b["title"], "verdict": b["verdict"]}
        if b["verdict"] not in ("NO SIGNAL", "UNTAGGED"):
            row["on_plan_pct"] = b["on_plan_pct"]
        row["note"] = said["note"].strip()
        plan_blocks.append(row)
    sinks = [{"what": s["what"], "minutes": s["minutes"], "note": said["note"].strip()} for s, said in zip(p["sinks"], out["time_sinks"])]
    asked = {u["label"] for u in p["unmapped"]}
    tags = [{"label": t["label"], "category": t["category"]} for t in out.get("title_tags") or []
            if isinstance(t, dict) and t.get("label") in asked and t.get("category") in ALLOWED]
    return {
        "facts": {"plan_blocks": plan_blocks, "time_sinks": sinks, "best_stretch": (out.get("best_stretch") or None),
                  "anomalies": [a.strip() for a in out["anomalies"]]},
        "summary": out["summary"].strip(), "title_tags": tags,
    }


async def generate_digest(client: LLMClient, practiced: dict, *, retries: int = RETRIES) -> dict:
    messages = [Message("system", SYSTEM), Message("user", render(practiced))]
    problems: list[str] = []
    for attempt in range(retries + 1):
        try:
            out, done = await call_json(client, messages, max_tokens=MAX_TOKENS, temperature=0.2)
            reply = done.text
        except BadReply as e:
            out, reply, problems = None, e.text, [str(e)]
        else:
            problems = validate(out, practiced)
            if not problems:
                return assemble(out, practiced)
        if attempt == retries:
            break
        messages += [Message("assistant", reply), Message("user", "That answer cannot be used:\n- " + "\n- ".join(problems) +
                                                           "\nAnswer again with the one JSON object, fixed.")]
    raise DigestError("digest rejected after retries: " + "; ".join(problems))
