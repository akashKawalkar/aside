"""Run synthetic memory evals with FakeClient by default, or an explicitly selected gated Gemini profile.

This exercises prompt assembly, session boundaries, checks, and reporting. Its
fixed fake replies are plumbing fixtures, not evidence of Gemini quality.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import storage
from config import load_config
from evals.constraints import CONSTRAINTS
from llm.approved import ApprovedClient, Grant, approved
from llm.client import LLMClient, Message, load_profile
from llm.fake import FakeClient
from llm.factory import create_client
from llm.quota import provider_day_start

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = ROOT / "evals" / "scenarios.json"
QUESTIONS_PATH = ROOT / "evals" / "questions.json"


def _fake_reply(scenario_id: str, pinned: bool):
    def reply(messages: list[Message]) -> str:
        if scenario_id == "constraint_carryover":
            turn = int(messages[-1].content.split("turn=")[-1].split()[0])
            if turn == 1:
                return "Understood."
            if pinned:
                return "Choose one task and work on it for 25 minutes."
            return "You could try focusing on one task for twenty-five minutes, taking a short break afterward, and writing distractions down as they come up. 🙂"
        if scenario_id == "vegetarian_preference":
            if "session=2" not in messages[-1].content:
                return "Noted."
            if pinned:
                return "Try a vegetarian chickpea curry with rice."
            return "Try chicken tacos with salsa."
        raise ValueError(f"no fake reply fixture for {scenario_id!r}")

    return reply


def _client(scenario_id: str, pinned: bool, client: LLMClient | None = None) -> LLMClient:
    return client or FakeClient(reply=_fake_reply(scenario_id, pinned))


def _system(scenario: dict[str, Any], pinned: bool, *, memory_available: bool = True) -> Message:
    if not pinned or not memory_available:
        return Message("system", "You are a concise personal assistant.")
    if scenario["kind"] == "constraint":
        memory = scenario["constraint"]
    else:
        memory = "The user prefers vegetarian meals."
    return Message("system", f"You are a concise personal assistant.\nPINNED MEMORY\n- {memory}")


def _checks(text: str, check_ids: list[str]) -> list[dict[str, Any]]:
    rows = []
    for check_id in check_ids:
        constraint = CONSTRAINTS[check_id]
        result = constraint.check(text)
        rows.append({"id": constraint.id, "passed": result.passed, "detail": result.detail})
    return rows


async def _complete(client: LLMClient, messages: list[Message], *, operation: str) -> str:
    # A supplied real client must be the gated client from llm.factory. FakeClient
    # accepts the same grant context, so both paths exercise this call boundary.
    if not isinstance(client, (FakeClient, ApprovedClient)):
        raise TypeError("eval clients must be FakeClient or ApprovedClient")
    with approved(Grant("user", operation=f"eval:{operation}")):
        completion = await client.complete(messages, max_tokens=300, temperature=0.0)
    return completion.text


async def run_constraint(scenario: dict[str, Any], pinned: bool, client: LLMClient | None = None) -> dict[str, Any]:
    client = _client(scenario["id"], pinned, client)
    history: list[Message] = []
    report = {"scenario": scenario["id"], "pinning": pinned, "calls": 0, "turns": []}
    checks_at = set(scenario["checks"])
    for turn in range(1, max(checks_at) + 1):
        if scenario.get("session_boundary_before_checks") and turn in checks_at:
            history = []
        # The user's constraint is learned at turn 1. Pinned memory can carry it
        # from turn 2 onward, including after a simulated session boundary.
        system = _system(scenario, pinned, memory_available=(turn > scenario["seed_turn"]))
        if not history:
            history.append(system)
        if turn == scenario["seed_turn"]:
            prompt = f"Please remember this constraint: {scenario['constraint']} EVAL turn={turn}"
        else:
            prompt = f"{scenario['prompt']} EVAL turn={turn}"
        history.append(Message("user", prompt))
        text = await _complete(client, history, operation=scenario["id"])
        report["calls"] += 1
        history.append(Message("assistant", text))
        if turn in checks_at:
            checked = _checks(text, scenario["check_ids"])
            report["turns"].append({"turn": turn, "response": text, "checks": checked,
                                    "passed": all(row["passed"] for row in checked)})
    report["passed"] = all(row["passed"] for row in report["turns"])
    return report


async def run_cross_session(scenario: dict[str, Any], pinned: bool, client: LLMClient | None = None) -> dict[str, Any]:
    client = _client(scenario["id"], pinned, client)
    # Nothing is pinned before session 1 states the preference. Only session 2
    # gets the saved memory, which is the behavior this scenario measures.
    first = [_system(scenario, False), Message("user", f"session=1 {scenario['session_1']}")]
    await _complete(client, first, operation=scenario["id"])
    second = [_system(scenario, pinned), Message("user", f"session=2 {scenario['session_2']}")]
    text = await _complete(client, second, operation=scenario["id"])
    checked = _checks(text, scenario["check_ids"])
    return {"scenario": scenario["id"], "pinning": pinned, "calls": 2,
            "turns": [{"session": 2, "response": text, "checks": checked,
                       "passed": all(row["passed"] for row in checked)}],
            "passed": all(row["passed"] for row in checked)}


async def run_suite(
    *, pinning: str = "both", scenario_ids: set[str] | None = None,
    client_factory=None, mode: str = "fake",
) -> dict[str, Any]:
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    if scenario_ids:
        scenarios = [s for s in scenarios if s["id"] in scenario_ids]
        found = {s["id"] for s in scenarios}
        missing = scenario_ids - found
        if missing:
            raise ValueError(f"unknown scenario ids: {', '.join(sorted(missing))}")
    modes = [True, False] if pinning == "both" else [pinning == "on"]
    results = []
    for enabled in modes:
        for scenario in scenarios:
            client = client_factory(scenario["id"], enabled) if client_factory else None
            if scenario["kind"] == "constraint":
                result = await run_constraint(scenario, enabled, client)
            elif scenario["kind"] == "cross_session":
                result = await run_cross_session(scenario, enabled, client)
            else:
                raise ValueError(f"unknown scenario kind: {scenario['kind']}")
            results.append(result)
    checks = [turn for result in results for turn in result["turns"]]
    passed = sum(bool(turn["passed"]) for turn in checks)
    note = "Fake replies validate the eval harness only; they do not measure model quality." if mode == "fake" else "Real model run; all calls used ApprovedClient and the user grant."
    return {"mode": mode, "note": note,
            "question_count": len(json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))),
            "summary": {"scenarios": len(results), "checked_turns": len(checks), "passed_turns": passed,
                        "adherence": (passed / len(checks)) if checks else 1.0},
            "results": results}


def _print_report(report: dict[str, Any]) -> None:
    if report.get("kind") == "standard_questions":
        print(f"{report['mode']}: {report['summary']['questions']} qualitative question(s); "
              f"{report['summary']['calls']} calls. Review overall feel before the listed criteria.")
        for row in report["results"]:
            print(f"\n{row['id']} ({row['category']}): {row['question']}")
            print("  response: " + row["response"].encode("ascii", "backslashreplace").decode("ascii"))
            print(f"  feel_rating: {row['feel_rating']!r}")
            for criterion in row["criteria"]:
                print(f"  review: {criterion}")
        return

    summary = report["summary"]
    print(f"{report['mode']} eval: {summary['passed_turns']}/{summary['checked_turns']} checked turns pass; "
          f"adherence={summary['adherence']:.0%}; {report['question_count']} standard questions loaded.")
    print(report["note"])
    for result in report["results"]:
        print(f"\n{result['scenario']} (pinning={'on' if result['pinning'] else 'off'}): "
              f"{'PASS' if result['passed'] else 'FAIL'}; {result['calls']} calls")
        for turn in result["turns"]:
            label = f"turn {turn['turn']}" if "turn" in turn else f"session {turn['session']}"
            print(f"  {label}: {'PASS' if turn['passed'] else 'FAIL'}")
            for check in turn["checks"]:
                print(f"    {'ok' if check['passed'] else 'FAIL'} {check['id']}: {check['detail']}")


async def _run_model_suite(args: argparse.Namespace) -> dict[str, Any]:
    """Run only explicitly selected synthetic scenarios through the normal quota and trace gate."""
    if bool(args.scenario) == bool(args.question):
        raise ValueError("choose either --scenario or --question for a model run")

    questions = []
    if args.question:
        if len(args.question) > 3:
            raise ValueError("select at most three standard questions for a rationed model run")
        if len(set(args.question)) != len(args.question):
            raise ValueError("the same question cannot be selected more than once")
        question_bank = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
        by_id = {question["id"]: question for question in question_bank}
        missing = set(args.question) - by_id.keys()
        if missing:
            raise ValueError(f"unknown question ids: {', '.join(sorted(missing))}")
        questions = [by_id[question_id] for question_id in args.question]
        requested_calls = len(questions)
    else:
        if not args.scenario:
            raise ValueError("model runs require an explicit --scenario or --question")
        if len(args.scenario) > 2:
            raise ValueError("select at most two scenarios for a rationed model run")
        if args.pinning == "both":
            raise ValueError("scenario model runs require --pinning on or --pinning off; both can exceed the daily budget")
        if len(set(args.scenario)) != len(args.scenario):
            raise ValueError("the same scenario cannot be selected more than once")
        scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
        by_id = {scenario["id"]: scenario for scenario in scenarios}
        missing = set(args.scenario) - by_id.keys()
        if missing:
            raise ValueError(f"unknown scenario ids: {', '.join(sorted(missing))}")
        turns = [by_id[scenario_id] for scenario_id in args.scenario]
        requested_calls = sum(max(s["checks"]) if s["kind"] == "constraint" else 2 for s in turns)

    cfg = load_config()
    profile = load_profile(args.model)
    if profile.provider != "gemini":
        raise ValueError("the opt-in eval model must use the Gemini provider")
    pool = storage.make_pool()
    await pool.open()
    try:
        async def count_today() -> int:
            return await storage.count_llm_traces_since(pool, provider_day_start(datetime.now(timezone.utc)))

        async def save_span(span: dict[str, Any]) -> None:
            await storage.insert_llm_trace(pool, span)

        used = await count_today()
        reserve = 5
        available = max(0, cfg.llm.daily_call_cap - used)
        if requested_calls + reserve > available:
            raise ValueError(
                f"run needs {requested_calls} calls and preserves {reserve} calls of headroom; "
                f"provider-day budget has {available} remaining ({used}/{cfg.llm.daily_call_cap} used)"
            )

        client = create_client(
            profile,
            count_today=count_today,
            daily_call_cap=cfg.llm.daily_call_cap,
            background_enabled=False,
            save_span=save_span,
            timeout=cfg.llm.attempt_timeout,
        )
        if not isinstance(client, ApprovedClient):
            raise TypeError("eval model runs require a single gated client without a fallback")

        def client_factory(_scenario_id: str, _pinned: bool) -> LLMClient:
            return client

        if questions:
            results = []
            system = Message("system", "You are a concise personal assistant. Answer the user's request directly.")
            for question in questions:
                response = await _complete(
                    client,
                    [system, Message("user", question["question"])],
                    operation=f"standard_question:{question['id']}",
                )
                results.append({
                    "id": question["id"],
                    "category": question["category"],
                    "question": question["question"],
                    "criteria": question["criteria"],
                    "response": response,
                    "feel_rating": None,
                    "review_notes": None,
                })
            return {
                "mode": f"model:{profile.name}",
                "kind": "standard_questions",
                "note": "Qualitative review: judge overall feel first; criteria are prompts for reflection, not automatic scores.",
                "summary": {"questions": len(results), "calls": len(results)},
                "results": results,
            }

        return await run_suite(
            pinning=args.pinning,
            scenario_ids=set(args.scenario),
            client_factory=client_factory,
            mode=f"model:{profile.name}",
        )
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pinning", choices=("both", "on", "off"), default="both")
    parser.add_argument("--scenario", action="append", help="scenario id; repeat to select several")
    parser.add_argument("--question", action="append", help="standard question id for an opt-in qualitative model run; repeat up to three")
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    parser.add_argument("--model", help="opt into a quota-gated real Gemini run for selected synthetic eval inputs")
    args = parser.parse_args()
    if args.question and not args.model:
        parser.error("--question is for qualitative model runs; add --model gemini-3.6-flash")
    if args.model and not args.output:
        parser.error("model runs require --output so responses and measured cases are saved")
    if args.output and args.output.exists():
        parser.error(f"output already exists; choose a new path to avoid overwriting: {args.output}")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        report = asyncio.run(_run_model_suite(args)) if args.model else asyncio.run(
            run_suite(pinning=args.pinning, scenario_ids=set(args.scenario or []) or None)
        )
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    _print_report(report)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
