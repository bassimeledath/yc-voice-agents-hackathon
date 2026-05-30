"""Run text-only YC interview batches and judge them."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.game_config import build_agent, load_game_definition
from core.judge import METRICS, judge_transcript
from core.startups import format_startup_profile, generate_startup_profile, validate_profile
from core.text_bridge import run_text_bridge
from core.types import BridgeOptions

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a batch of YC-style text simulations.")
    parser.add_argument("--game", default="yc_interview")
    parser.add_argument("--profiles", type=int, default=3)
    parser.add_argument("--profile-file", type=Path, default=None)
    parser.add_argument("--startup-idea", action="append", default=[])
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--candidate-variant", default="dynamic_base")
    parser.add_argument("--candidate-prompt-file", type=Path, default=None)
    parser.add_argument("--interviewer-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--judge-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--time-limit-seconds", type=float, default=45.0)
    parser.add_argument("--max-tokens", type=int, default=130)
    parser.add_argument("--llm-timeout-seconds", type=float, default=12.0)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--copy-to-downloads", action="store_true")
    return parser.parse_args()


def load_profiles(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("profiles", [])
    if not isinstance(data, list):
        raise ValueError("Profile file must contain a list or an object with a profiles list")
    return [validate_profile(item) for item in data]


async def profiles_for_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.profile_file:
        return load_profiles(args.profile_file)[: args.profiles]

    profiles = []
    for index in range(1, args.profiles + 1):
        idea = args.startup_idea[index - 1] if index <= len(args.startup_idea) else None
        profiles.append(await generate_startup_profile(idea=idea, index=index))
    return profiles


async def run_one(args: argparse.Namespace, profile: dict[str, Any], index: int) -> dict[str, Any]:
    startup_context = format_startup_profile(profile)
    game = load_game_definition(args.game)
    starts = game.get("starts", "right")
    prompt_text_override = (
        args.candidate_prompt_file.read_text().strip()
        if getattr(args, "candidate_prompt_file", None)
        else None
    )

    candidate = build_agent(
        game=args.game,
        side="candidate",
        name="candidate",
        stack=args.candidate_stack,
        prompt_variant=args.candidate_variant,
        prompt_text_override=prompt_text_override,
        runtime_context_title="startup profile",
        runtime_context=startup_context,
    )
    interviewer = build_agent(
        game=args.game,
        side="interviewer",
        name="interviewer",
        stack=args.interviewer_stack,
    )

    run = await run_text_bridge(
        left=candidate,
        right=interviewer,
        opening_message=(
            "Ask the founder for a crisp one or two sentence elevator pitch, "
            "then continue with probing YC-style questions."
        ),
        options=BridgeOptions(
            turns=args.turns,
            time_limit_seconds=args.time_limit_seconds,
            max_tokens=args.max_tokens,
            llm_timeout_seconds=args.llm_timeout_seconds,
            voice_timeout_seconds=0.0,
            starts=starts,
            output=None,
            audio_dir=None,
        ),
    )
    run["game"] = {"id": args.game, "name": game.get("name", args.game)}
    run["startup_profile"] = profile
    run["batch_index"] = index
    judgment = await judge_transcript(
        startup_profile=profile,
        run=run,
        judge_stack=args.judge_stack,
    )
    return {"run": run, "judgment": judgment}


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    metric_averages = {}
    for metric in METRICS:
        metric_averages[metric] = average(
            [
                float(item["judgment"]["scores"][metric]["score"])
                for item in results
                if metric in item["judgment"].get("scores", {})
            ]
        )
    unsupported_claims = [
        claim
        for item in results
        for claim in item["judgment"].get("unsupported_claims", [])
    ]
    run_error_count = sum(
        1
        for item in results
        if any(turn.get("status") != "ok" for turn in item["run"].get("turns", []))
    )
    return {
        "runs": len(results),
        "run_error_count": run_error_count,
        "overall_average": average([float(item["judgment"]["overall_score"]) for item in results]),
        "metric_averages": metric_averages,
        "unsupported_claim_count": len(unsupported_claims),
        "unsupported_claim_examples": unsupported_claims[:8],
    }


def write_outputs(args: argparse.Namespace, profiles: list[dict[str, Any]], results: list[dict[str, Any]]):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path("runs") / args.game / f"batch-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")
    for index, item in enumerate(results, start=1):
        run_dir = output_dir / f"run-{index:03d}"
        run_dir.mkdir()
        (run_dir / "transcript.json").write_text(json.dumps(item["run"], indent=2) + "\n")
        (run_dir / "judge.json").write_text(json.dumps(item["judgment"], indent=2) + "\n")

    summary = summarize(results)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "summary.md").write_text(render_markdown(args, summary, profiles, results))

    if args.copy_to_downloads:
        downloads_dir = Path.home() / "Downloads" / output_dir.name
        if downloads_dir.exists():
            shutil.rmtree(downloads_dir)
        shutil.copytree(output_dir, downloads_dir)
        print(f"Copied report to {downloads_dir}")

    print(f"Wrote batch outputs to {output_dir}")


def render_markdown(
    args: argparse.Namespace,
    summary: dict[str, Any],
    profiles: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> str:
    lines = [
        "# Hotseat YC Batch Eval",
        "",
        f"Candidate: `{args.candidate_stack}/{args.candidate_variant}`",
        f"Candidate prompt file: `{args.candidate_prompt_file}`",
        f"Interviewer: `{args.interviewer_stack}`",
        f"Judge: `{args.judge_stack}`",
        "",
        "## Aggregate Scores",
        "",
        f"- Overall average: {summary['overall_average']}/10",
        f"- Run errors: {summary['run_error_count']}",
        f"- Unsupported claims flagged: {summary['unsupported_claim_count']}",
        "",
    ]
    for metric, score in summary["metric_averages"].items():
        lines.append(f"- {metric}: {score}/10")

    lines.extend(["", "## Runs", ""])
    for index, (profile, item) in enumerate(zip(profiles, results, strict=True), start=1):
        judgment = item["judgment"]
        lines.extend(
            [
                f"### Run {index}: {profile['company']}",
                "",
                profile["one_liner"],
                "",
                f"Overall: {judgment['overall_score']}/10",
                "",
                judgment.get("summary", ""),
                "",
                "Scores:",
            ]
        )
        for metric in METRICS:
            metric_result = judgment["scores"][metric]
            lines.append(
                f"- {metric}: {metric_result['score']}/10 - {metric_result['reason']}"
            )
        claims = judgment.get("unsupported_claims", [])
        if claims:
            lines.extend(["", "Unsupported claims:"])
            for claim in claims[:4]:
                lines.append(
                    f"- {claim.get('severity', 'unknown')}: {claim.get('claim')} "
                    f"({claim.get('why_unsupported')})"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def main_async(args: argparse.Namespace) -> None:
    profiles = await profiles_for_args(args)
    results = []
    for index, profile in enumerate(profiles, start=1):
        print(f"\n=== Batch run {index}/{len(profiles)}: {profile['company']} ===")
        results.append(await run_one(args, profile, index))
    write_outputs(args, profiles, results)


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
