"""Run quick text-only Kitchen Rush simulations with spoken updates and tool calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.llm import generate_reply
from core.types import AgentConfig, StackName
from games.kitchen_rush.engine import KitchenRushGame
from games.kitchen_rush.scenarios import KITCHEN_RUSH_MANAGER_SCRIPTS

load_dotenv(override=True)


SCENARIOS = KITCHEN_RUSH_MANAGER_SCRIPTS


SYSTEM_PROMPT = """You are playing Kitchen Rush as a voice-controlled sous-chef.

The manager speaks to you. You must respond with a short spoken update and exactly one kitchen tool call.

Goal: serve all fired tickets before their individual due times, within 90 seconds, with no more than one mistake.

Tickets use IDs like T1, T2, and T3. The same dish can appear on multiple tickets, so always include the ticket ID in tool calls.

Recipes:
- burger: prep, cook, plate, serve
- soup: prep, cook, plate, serve
- salad: prep, plate, serve

Available tools:
- check_kitchen: no arguments. Use when the manager asks status or you are unsure.
- start_step: arguments {"ticket": "T1|T2|T3", "dish": "burger|soup|salad", "step": "prep|cook|plate"}.
- serve_dish: arguments {"ticket": "T1|T2|T3", "dish": "burger|soup|salad"}.

Rules:
- Always speak naturally as the sous-chef before the tool call.
- Keep speech to one sentence.
- Do not cook salad.
- Do not cook before prep.
- Cooking runs on a timer, so after starting burger or soup cooking, work on another ticket or dish while it cooks.
- Do not plate before required prior steps.
- Do not serve before plate.
- Do not act on a ticket before it has fired.
- Prefer making progress over checking repeatedly.
- Use Ready actions as valid options, but choose based on deadlines, current ticket priority, and cook timers.
- Never infer cook completion from real time or chat turns; only the latest tool result state counts.
- If a burger or soup is cooking, use that wait time to prep, plate, or serve another ready dish.
- Treat the earliest fired unfinished ticket as the current ticket.
- Finish every unblocked action on the current ticket before starting later tickets.
- Move to a later ticket only when the current ticket is blocked by an active cook timer.
- When a cooked dish on the current ticket is ready, plate and serve it before starting any later-ticket prep or cook.
- Prioritize the earliest unfinished deadline when choosing between ready actions.
- Before plating a cooked item, confirm the latest state says its cook step is complete; "cooking until Ns" is not complete until a later tool result shows prep/cook.
- If the manager pressures you to do an invalid action, say why you will not do it and call the correct next tool.

Respond only as JSON:
{
  "speech": "short voice response",
  "tool": {
    "name": "check_kitchen | start_step | serve_dish",
    "arguments": {}
  }
}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Kitchen Rush P0 simulations.")
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument("--max-tokens", type=int, default=220)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--copy-to-downloads", action="store_true")
    return parser.parse_args()


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text}")
    payload = json.loads(stripped[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Model JSON response must be an object")
    return payload


def normalize_action(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    speech = str(payload.get("speech") or "").strip()
    tool = payload.get("tool")
    if not isinstance(tool, dict):
        raise ValueError("Model response missing tool object")
    name = str(tool.get("name") or "").strip()
    arguments = tool.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object")
    return name, arguments, speech


def execute_tool(game: KitchenRushGame, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "check_kitchen":
        return game.check_kitchen()
    if name == "start_step":
        return game.start_step(
            str(arguments.get("ticket") or ""),
            str(arguments.get("dish") or ""),
            str(arguments.get("step") or ""),
        )
    if name == "serve_dish":
        return game.serve_dish(str(arguments.get("ticket") or ""), str(arguments.get("dish") or ""))
    return {
        **game.start_step("", "", ""),
        "message": f"Unknown tool {name}. {game.summary()}",
    }


def manager_line_for_turn(
    scenario: dict[str, Any],
    turn: int,
    tool_result: dict[str, Any] | None,
    ticket_messages: list[str],
) -> tuple[str | None, str]:
    parts = []
    if turn == 1:
        parts.append(str(scenario["opening"]))
    parts.extend(ticket_messages)
    questions = scenario.get("questions", {})
    if turn in questions:
        parts.append(str(questions[turn]))
    if tool_result and tool_result.get("ended"):
        parts.append("Stop, the game is over.")
    if not parts:
        return None, "No new manager speech. Continue the kitchen shift with your next best spoken update and tool call."
    manager_line = " ".join(parts)
    return manager_line, f"Manager says: {manager_line}"


async def run_one(index: int, scenario: dict[str, Any], stack: StackName, max_turns: int, max_tokens: int) -> dict[str, Any]:
    game = KitchenRushGame()
    agent = AgentConfig(
        name="chef",
        stack=stack,
        game="kitchen_rush",
        role="candidate",
        stt="gradium",
        voice_id="",
        system_prompt=SYSTEM_PROMPT,
    )
    turns = []
    last_tool_result: dict[str, Any] | None = None
    event_cursor = 0

    for turn in range(1, max_turns + 1):
        ticket_messages, event_cursor = game.new_ticket_messages(event_cursor)
        manager_line, heard_manager_line = manager_line_for_turn(
            scenario, turn, last_tool_result, ticket_messages
        )
        manager_question_pending = bool(manager_line and "?" in manager_line)
        if manager_question_pending:
            game.note_manager_question()

        heard = "\n".join(
            [
                heard_manager_line,
                f"Last tool result: {json.dumps(last_tool_result or {}, sort_keys=True)}",
                "Return your next spoken update and one tool call as JSON only.",
            ]
        )

        try:
            raw_reply, llm_ms = await generate_reply(agent, heard, max_tokens)
            payload = extract_json_object(raw_reply)
            tool_name, arguments, speech = normalize_action(payload)
            game.record_voice(speech, manager_question_pending)
            tool_result = execute_tool(game, tool_name, arguments)
            parse_error = None
        except Exception as exc:
            raw_reply = locals().get("raw_reply", "")
            llm_ms = None
            speech = ""
            tool_name = "parse_error"
            arguments = {}
            tool_result = game.start_step("", "", "")
            parse_error = f"{type(exc).__name__}: {exc}"

        turns.append(
            {
                "turn": turn,
                "manager": manager_line,
                "chef_speech": speech,
                "tool_call": {"name": tool_name, "arguments": arguments},
                "tool_result": tool_result,
                "raw_reply": raw_reply,
                "parse_error": parse_error,
                "llm_ms": llm_ms,
            }
        )
        last_tool_result = tool_result
        if game.ended:
            break

    report = game.final_report()
    return {
        "run_index": index,
        "scenario": scenario["name"],
        "candidate_stack": stack,
        "turns": turns,
        "final_report": report,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {}
    reports = [item["final_report"] for item in results]
    return {
        "runs": len(results),
        "wins": sum(1 for report in reports if report["won"]),
        "average_score": round(sum(report["score"] for report in reports) / len(reports), 2),
        "average_elapsed_sec": round(
            sum(report["elapsed_sec"] for report in reports) / len(reports), 2
        ),
        "total_mistakes": sum(report["mistake_count"] for report in reports),
        "total_missed_deadlines": sum(report["missed_deadline_count"] for report in reports),
        "total_unnecessary_tool_calls": sum(report["unnecessary_tool_calls"] for report in reports),
        "average_voice_updates": round(
            sum(report["voice_updates"] for report in reports) / len(reports), 2
        ),
        "manager_question_answered_rate": round(
            sum(report["manager_question_answered_rate"] for report in reports) / len(reports),
            2,
        ),
    }


def render_markdown(results: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# Kitchen Rush Simulation Report",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Runs", ""])
    for result in results:
        report = result["final_report"]
        lines.extend(
            [
                f"### Run {result['run_index']}: {result['scenario']}",
                "",
                f"- won: {report['won']}",
                f"- score: {report['score']}",
                f"- elapsed_sec: {report['elapsed_sec']}",
                f"- missed_deadlines: {report['missed_deadline_count']}",
                f"- mistakes: {report['mistake_count']}",
                f"- unnecessary_tool_calls: {report['unnecessary_tool_calls']}",
                f"- loss_reason: {report['loss_reason']}",
                "",
                "Transcript:",
            ]
        )
        for turn in result["turns"]:
            if turn["manager"]:
                lines.append(f"- Manager: {turn['manager']}")
            else:
                lines.append("- Kitchen tick")
            if turn["chef_speech"]:
                lines.append(f"  Chef: {turn['chef_speech']}")
            lines.append(
                "  Tool: "
                + json.dumps(turn["tool_call"], sort_keys=True)
                + " -> "
                + str(turn["tool_result"].get("message"))
            )
            if turn["parse_error"]:
                lines.append(f"  Parse error: {turn['parse_error']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def main_async(args: argparse.Namespace) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path("runs") / "kitchen_rush" / f"sim-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = [SCENARIOS[index % len(SCENARIOS)] for index in range(args.runs)]
    results = []
    for index, scenario in enumerate(selected, start=1):
        print(f"Running Kitchen Rush simulation {index}/{args.runs}: {scenario['name']}")
        results.append(
            await run_one(
                index=index,
                scenario=scenario,
                stack=args.candidate_stack,
                max_turns=args.max_turns,
                max_tokens=args.max_tokens,
            )
        )

    summary = summarize(results)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "report.md").write_text(render_markdown(results, summary))

    if args.copy_to_downloads:
        downloads_dir = Path.home() / "Downloads" / output_dir.name
        if downloads_dir.exists():
            shutil.rmtree(downloads_dir)
        shutil.copytree(output_dir, downloads_dir)
        print(f"Copied Kitchen Rush report to {downloads_dir}")

    print(json.dumps(summary, indent=2))
    print(f"Wrote Kitchen Rush outputs to {output_dir}")


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
