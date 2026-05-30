#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from games.kitchen_rush.scenarios import KITCHEN_RUSH_MANAGER_SCRIPTS

API_BASE_URL = "https://api.cekura.ai/test_framework/v1"
DEFAULT_ENV_FILE = Path(__file__).with_name(".env")


def load_env(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_int_list(values: list[str] | None, env_name: str) -> list[int]:
    raw_values = values or []
    if not raw_values:
        raw_values = [os.getenv(env_name, "")]

    ids: list[int] = []
    for value in raw_values:
        for item in value.split(","):
            item = item.strip()
            if item:
                ids.append(int(item))
    return ids


def parse_string_list(values: list[str] | None, env_name: str) -> list[str]:
    raw_values = values or []
    if not raw_values:
        raw_values = [os.getenv(env_name, "")]

    items: list[str] = []
    for value in raw_values:
        for item in value.split(","):
            item = item.strip()
            if item:
                items.append(item)
    return items


def request_json(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    base_url: str,
) -> Any:
    api_key = os.getenv("CEKURA_API_KEY")
    if not api_key:
        raise SystemExit("CEKURA_API_KEY is required. Add it to server/.env or export it.")

    query = {key: value for key, value in (query or {}).items() if value not in (None, "")}
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"

    data = None
    headers = {"X-CEKURA-API-KEY": api_key}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Cekura API error {exc.code}: {details}") from exc

    return json.loads(payload) if payload else None


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def list_agents(args: argparse.Namespace) -> None:
    print_json(
        request_json(
            "GET",
            "/aiagents/",
            query={"project_id": args.project_id, "page_size": args.page_size},
            base_url=args.base_url,
        )
    )


def list_evaluators(args: argparse.Namespace) -> None:
    agent_id = args.agent_id or os.getenv("CEKURA_AGENT_ID")
    print_json(
        request_json(
            "GET",
            "/scenarios/",
            query={
                "agent_id": agent_id,
                "assistant_id": args.assistant_id,
                "project_id": args.project_id,
                "tags": args.tags,
                "page_size": args.page_size,
            },
            base_url=args.base_url,
        )
    )


def get_result(args: argparse.Namespace) -> None:
    result_id = args.result_id or os.getenv("CEKURA_RESULT_ID")
    if not result_id:
        raise SystemExit("Provide --result-id or set CEKURA_RESULT_ID in server/.env.")

    print_json(
        request_json(
            "GET",
            f"/results-external/{result_id}/",
            base_url=args.base_url,
        )
    )


def run_text(args: argparse.Namespace) -> None:
    scenario_ids = parse_int_list(args.scenario, "CEKURA_SCENARIO_IDS")
    tags = parse_string_list(args.tag, "CEKURA_TAGS")
    if not scenario_ids and not tags and not args.folder_path:
        raise SystemExit("Provide --scenario IDs, --tag values, or --folder-path.")

    body: dict[str, Any] = {
        "agent_id": args.agent_id or os.getenv("CEKURA_AGENT_ID"),
        "assistant_id": args.assistant_id,
        "name": args.name,
        "frequency": args.frequency,
        "websocket_url": args.websocket_url or os.getenv("CEKURA_WEBSOCKET_URL"),
        "concurrency_limit": args.concurrency_limit,
    }
    if scenario_ids:
        body["scenarios"] = scenario_ids
    if tags:
        body["tags"] = tags
    if args.folder_path:
        body["folder_path"] = args.folder_path
    if args.project_id:
        body["project_id"] = args.project_id

    print_json(
        request_json(
            "POST",
            "/scenarios/run_scenarios_text/",
            body=body,
            base_url=args.base_url,
        )
    )


def create_evaluators(args: argparse.Namespace) -> None:
    payloads = json.loads(args.payload_file.read_text())
    if not isinstance(payloads, list):
        raise SystemExit("Payload file must contain a JSON list of evaluator create payloads.")

    created = []
    for payload in payloads:
        if not isinstance(payload, dict):
            raise SystemExit("Each evaluator payload must be an object.")
        if args.agent_id and not payload.get("agent"):
            payload["agent"] = args.agent_id
        if args.project_id and not payload.get("project"):
            payload["project"] = args.project_id
        if args.personality_id and not payload.get("personality"):
            payload["personality"] = args.personality_id
        created.append(
            request_json(
                "POST",
                "/scenarios/",
                body=payload,
                base_url=args.base_url,
            )
        )

    print_json(created)


def run_pipecat(args: argparse.Namespace) -> None:
    scenario_ids = parse_int_list(args.scenario, "CEKURA_SCENARIO_IDS")
    if not scenario_ids:
        raise SystemExit("Provide --scenario IDs or set CEKURA_SCENARIO_IDS in server/.env.")

    print_json(
        request_json(
            "POST",
            "/scenarios/run_scenarios_pipecat_v2/",
            body={
                "scenarios": [{"scenario": scenario_id} for scenario_id in scenario_ids],
                "frequency": args.frequency,
            },
            base_url=args.base_url,
        )
    )


def load_profile_list(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("profiles", [])
    if not isinstance(data, list):
        raise SystemExit("Profile file must contain a list or an object with a profiles list.")
    return data


def yc_expected_outcome(profile: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Strictly evaluate the founder's answers using the hidden startup profile.",
            "The candidate should give a concise elevator pitch.",
            "The candidate should accurately explain the customer, problem, product, traction, pricing, go-to-market motion, and why-now using only profile-supported facts.",
            "The candidate should be candid about known weaknesses and should not invent precise metrics, customer commitments, revenue, pilots, regulatory claims, or market facts not present in the profile.",
            "Scoring dimensions: clear matter-of-fact explanation; users/metrics/learning; candid obstacles/specific insight.",
            "",
            "Hidden startup profile:",
            json.dumps(profile, indent=2, sort_keys=True),
        ]
    )


def yc_instructions(profile: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are a strict YC-style interviewer running a short founder interview.",
            "First ask for a crisp one or two sentence elevator pitch.",
            "Then ask probing follow-up questions about customers, urgency, traction, metrics, distribution, weaknesses, and what the founder has learned.",
            "Do not reveal the scoring rubric or hidden profile.",
            "Keep the interview moving quickly and end after you have probed several concrete claims.",
            "",
            f"Startup under interview: {profile.get('company', 'unknown')}",
            f"One-liner for interviewer context: {profile.get('one_liner', '')}",
        ]
    )


def export_yc_scenarios(args: argparse.Namespace) -> None:
    profiles = load_profile_list(args.profile_file)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = []
    test_profiles = []
    for index, profile in enumerate(profiles[: args.limit], start=1):
        name = f"YC Hotseat {index}: {profile.get('company', 'Startup')}"
        scenario = {
            "name": name,
            "scenario_type": "instruction",
            "instructions": yc_instructions(profile),
            "expected_outcome_prompt": yc_expected_outcome(profile),
            "tags": [args.tag, f"{args.tag}:yc_interview"],
            "folder_path": args.folder_path,
        }
        if args.agent_id:
            scenario["agent"] = args.agent_id
        if args.project_id:
            scenario["project"] = args.project_id
        if args.personality_id:
            scenario["personality"] = args.personality_id
        if args.metric:
            scenario["metrics"] = args.metric

        test_profile = {
            "name": f"{name} profile",
            "information": {
                "X-Startup-Profile-Index": str(index),
                "company": profile.get("company"),
                "one_liner": profile.get("one_liner"),
            },
        }
        scenarios.append(scenario)
        test_profiles.append(test_profile)

    (output_dir / "cekura_scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n")
    (output_dir / "cekura_test_profiles.json").write_text(
        json.dumps(test_profiles, indent=2) + "\n"
    )
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# Cekura YC Scenario Export",
                "",
                "These payloads mirror the local transcript batch as Cekura text evaluators.",
                "",
                "- `cekura_scenarios.json`: scenario create payloads.",
                "- `cekura_test_profiles.json`: companion test-profile payloads that send `X-Startup-Profile-Index` to the WebSocket text agent.",
                "",
                "Run the local text agent before launching Cekura text tests:",
                "",
                "```bash",
                "uv run python cekura_text_agent.py --candidate-prompt-file <prompt.md> --profile-file <profiles.json>",
                "```",
                "",
                "Expose it with ngrok and set the resulting `wss://.../cekura` URL in Cekura or pass it to:",
                "",
                "```bash",
                "python cekura_cli.py run-text --scenario <id> --websocket-url wss://example.ngrok-free.app/cekura",
                "```",
                "",
            ]
        )
    )
    print(f"Wrote Cekura scenario export to {output_dir}")


def kitchen_rush_instructions(script: dict[str, Any], max_turns: int) -> str:
    questions = script.get("questions", {})
    question_lines = "\n".join(
        f"- Turn {turn}: {question}" for turn, question in sorted(questions.items())
    )
    return "\n".join(
        [
            "You are the kitchen manager in a short Kitchen Rush test.",
            "Your job is to drive the candidate chef through a 60-90 second lunch rush.",
            "Start with the opening line exactly as written below.",
            "After each chef response, send either the scheduled question for that turn or the phrase: Kitchen tick.",
            "Do not reveal recipes, deadlines, scoring criteria, or optimal actions beyond normal manager pressure.",
            "Stop when the chef includes FINAL_REPORT in its response, or after the maximum turn count.",
            "",
            f"Opening line: {script['opening']}",
            f"Maximum turns: {max_turns}",
            "",
            "Scheduled manager questions:",
            question_lines or "- None",
        ]
    )


def kitchen_rush_expected_outcome() -> str:
    return "\n".join(
        [
            "Evaluate the Kitchen Rush run strictly from the transcript.",
            "The source of truth is the FINAL_REPORT JSON in the candidate transcript.",
            "The single expected outcome is fully satisfied only when FINAL_REPORT is present and all of these are true: won=true, missed_deadline_count=0, mistake_count=0, and unnecessary_tool_calls <= 1.",
            "If those conditions are all true, mark the expected outcome fully satisfied.",
            "If FINAL_REPORT is missing or any condition is false, mark the expected outcome failed.",
        ]
    )


def export_kitchen_rush_scenarios(args: argparse.Namespace) -> None:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios = []
    for script in KITCHEN_RUSH_MANAGER_SCRIPTS[: args.limit]:
        scenario = {
            "name": f"Kitchen Rush: {script['name']}",
            "scenario_type": "instruction",
            "instructions": kitchen_rush_instructions(script, args.max_turns),
            "expected_outcome_prompt": kitchen_rush_expected_outcome(),
            "tags": [args.tag, f"{args.tag}:{script['name']}"],
            "folder_path": args.folder_path,
        }
        if args.agent_id:
            scenario["agent"] = args.agent_id
        if args.project_id:
            scenario["project"] = args.project_id
        if args.personality_id:
            scenario["personality"] = args.personality_id
        if args.metric:
            scenario["metrics"] = args.metric
        scenarios.append(scenario)

    (output_dir / "cekura_scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n")
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                "# Cekura Kitchen Rush Scenario Export",
                "",
                "These payloads run Kitchen Rush as a Cekura text regression suite.",
                "",
                "Serve the local Kitchen Rush Cekura text agent:",
                "",
                "```bash",
                "uv run python cekura_kitchen_agent.py --candidate-stack nemotron",
                "```",
                "",
                "Expose it with ngrok:",
                "",
                "```bash",
                "ngrok http 127.0.0.1:8766",
                "```",
                "",
                "Use the resulting `wss://.../cekura` URL in Cekura, then run text scenarios:",
                "",
                "```bash",
                "python cekura_cli.py run-text --scenario <id> --websocket-url wss://example.ngrok-free.app/cekura",
                "```",
                "",
            ]
        )
    )
    print(f"Wrote Cekura Kitchen Rush scenario export to {output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small Cekura helper for this Pipecat project.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--base-url", default=API_BASE_URL)

    subcommands = parser.add_subparsers(dest="command", required=True)

    agents = subcommands.add_parser("list-agents", help="List Cekura agents.")
    agents.add_argument("--project-id")
    agents.add_argument("--page-size", type=int, default=20)
    agents.set_defaults(func=list_agents)

    evaluators = subcommands.add_parser("list-evaluators", help="List Cekura evaluators.")
    evaluators.add_argument("--agent-id")
    evaluators.add_argument("--assistant-id")
    evaluators.add_argument("--project-id")
    evaluators.add_argument("--tags")
    evaluators.add_argument("--page-size", type=int, default=50)
    evaluators.set_defaults(func=list_evaluators)

    result = subcommands.add_parser("get-result", help="Fetch a Cekura result by ID.")
    result.add_argument("--result-id")
    result.set_defaults(func=get_result)

    text = subcommands.add_parser("run-text", help="Run Cekura evaluators in text mode.")
    text.add_argument("--agent-id", type=int)
    text.add_argument("--assistant-id")
    text.add_argument("--name", default="Hotseat text regression")
    text.add_argument("--scenario", action="append", help="Scenario ID or comma-separated IDs.")
    text.add_argument("--tag", action="append", help="Scenario tag or comma-separated tags.")
    text.add_argument("--folder-path")
    text.add_argument("--project-id", type=int)
    text.add_argument("--frequency", type=int, default=1)
    text.add_argument("--websocket-url")
    text.add_argument("--concurrency-limit", type=int, default=2)
    text.set_defaults(func=run_text)

    create = subcommands.add_parser(
        "create-evaluators", help="Create Cekura evaluators from an exported JSON payload."
    )
    create.add_argument("--payload-file", type=Path, required=True)
    create.add_argument("--agent-id", type=int)
    create.add_argument("--project-id", type=int)
    create.add_argument("--personality-id", type=int)
    create.set_defaults(func=create_evaluators)

    pipecat = subcommands.add_parser("run-pipecat", help="Run Cekura evaluators via Pipecat.")
    pipecat.add_argument("--scenario", action="append", help="Scenario ID or comma-separated IDs.")
    pipecat.add_argument("--frequency", type=int, default=1)
    pipecat.set_defaults(func=run_pipecat)

    export = subcommands.add_parser(
        "export-yc-scenarios", help="Write Cekura scenario payloads for local YC profiles."
    )
    export.add_argument("--profile-file", type=Path, required=True)
    export.add_argument("--output-dir", type=Path, default=Path("runs") / "cekura_export")
    export.add_argument("--limit", type=int, default=5)
    export.add_argument("--agent-id", type=int)
    export.add_argument("--project-id", type=int)
    export.add_argument("--personality-id", type=int)
    export.add_argument("--metric", type=int, action="append", default=[])
    export.add_argument("--folder-path", default="Hotseat.YC")
    export.add_argument("--tag", default="hotseat-yc")
    export.set_defaults(func=export_yc_scenarios)

    kitchen_export = subcommands.add_parser(
        "export-kitchen-rush-scenarios",
        help="Write Cekura scenario payloads for Kitchen Rush.",
    )
    kitchen_export.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs") / "cekura_export" / "kitchen_rush",
    )
    kitchen_export.add_argument("--limit", type=int, default=len(KITCHEN_RUSH_MANAGER_SCRIPTS))
    kitchen_export.add_argument("--max-turns", type=int, default=24)
    kitchen_export.add_argument("--agent-id", type=int)
    kitchen_export.add_argument("--project-id", type=int)
    kitchen_export.add_argument("--personality-id", type=int)
    kitchen_export.add_argument("--metric", type=int, action="append", default=[])
    kitchen_export.add_argument("--folder-path", default="Hotseat.KitchenRush")
    kitchen_export.add_argument("--tag", default="kitchen-rush")
    kitchen_export.set_defaults(func=export_kitchen_rush_scenarios)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    load_env(args.env_file)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
