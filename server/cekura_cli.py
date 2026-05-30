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

    pipecat = subcommands.add_parser("run-pipecat", help="Run Cekura evaluators via Pipecat.")
    pipecat.add_argument("--scenario", action="append", help="Scenario ID or comma-separated IDs.")
    pipecat.add_argument("--frequency", type=int, default=1)
    pipecat.set_defaults(func=run_pipecat)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    load_env(args.env_file)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
