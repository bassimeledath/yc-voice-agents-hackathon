"""Run a small prompt-improvement loop over YC interview text batches."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import batch_eval
from batch_eval import render_markdown, summarize
from core.judge import METRICS
from core.llm import generate_reply
from core.startups import generate_startup_profile
from core.types import AgentConfig

load_dotenv(override=True)

DEFAULT_PROMPT = Path("games/yc_interview/prompts/founder_dynamic_base.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 3 prompt-improvement iterations.")
    parser.add_argument("--game", default="yc_interview")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--profiles", type=int, default=5)
    parser.add_argument("--parallelism", type=int, default=5)
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--interviewer-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--judge-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--improver-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--time-limit-seconds", type=float, default=45.0)
    parser.add_argument("--max-tokens", type=int, default=130)
    parser.add_argument("--llm-timeout-seconds", type=float, default=12.0)
    parser.add_argument("--seed-prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--copy-to-downloads", action="store_true")
    return parser.parse_args()


async def generate_profiles(count: int) -> list[dict[str, Any]]:
    tasks = [generate_startup_profile(index=index) for index in range(1, count + 1)]
    return await asyncio.gather(*tasks)


def batch_args(args: argparse.Namespace, prompt_file: Path, output_dir: Path) -> argparse.Namespace:
    return argparse.Namespace(
        game=args.game,
        profiles=args.profiles,
        profile_file=None,
        startup_idea=[],
        candidate_stack=args.candidate_stack,
        candidate_variant=prompt_file.stem,
        candidate_prompt_file=prompt_file,
        interviewer_stack=args.interviewer_stack,
        judge_stack=args.judge_stack,
        turns=args.turns,
        time_limit_seconds=args.time_limit_seconds,
        max_tokens=args.max_tokens,
        llm_timeout_seconds=args.llm_timeout_seconds,
        output_dir=output_dir,
        copy_to_downloads=False,
    )


async def run_parallel_batch(
    args: argparse.Namespace,
    prompt_file: Path,
    profiles: list[dict[str, Any]],
    output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_args = batch_args(args, prompt_file, output_dir)
    semaphore = asyncio.Semaphore(args.parallelism)

    async def guarded_run(index: int, profile: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            print(f"Running {output_dir.name} profile {index}: {profile['company']}")
            return await batch_eval.run_one(run_args, profile, index)

    results = await asyncio.gather(
        *(guarded_run(index, profile) for index, profile in enumerate(profiles, start=1))
    )
    batch_eval.write_outputs(run_args, profiles, results)
    summary = summarize(results)
    return results, summary


def render_patterns(results: list[dict[str, Any]]) -> str:
    lines = []
    for index, item in enumerate(results, start=1):
        judgment = item["judgment"]
        run = item["run"]
        profile = run["startup_profile"]
        candidate_turns = [
            turn
            for turn in run.get("turns", [])
            if turn.get("speaker") == "candidate" and turn.get("status") == "ok"
        ]
        if not candidate_turns:
            lines.append(
                f"Run {index}: {profile['company']} skipped for prompt improvement "
                "because no successful candidate turn was recorded."
            )
            lines.append("")
            continue
        lines.append(f"Run {index}: {profile['company']}")
        lines.append(f"Overall: {judgment['overall_score']}/10")
        lines.append(f"Summary: {judgment.get('summary', '')}")
        for metric in METRICS:
            metric_result = judgment["scores"][metric]
            lines.append(f"- {metric}: {metric_result['score']}/10 - {metric_result['reason']}")
        claims = judgment.get("unsupported_claims", [])
        if claims:
            lines.append("Unsupported claims:")
            for claim in claims[:3]:
                lines.append(
                    f"- {claim.get('severity')}: {claim.get('claim')} "
                    f"({claim.get('why_unsupported')})"
                )
        lines.append("")
    return "\n".join(lines).strip()


async def improve_prompt(
    *,
    current_prompt: str,
    results: list[dict[str, Any]],
    improver_stack: str,
) -> str:
    system_prompt = """\
You improve a YC interview founder-agent prompt from batch evaluation results.
Make the smallest useful prompt change based only on repeated failure patterns across the batch.
Do not add long examples. Do not overfit to company names, exact transcript wording, or one-off failures.
Return only the revised prompt text, no markdown fence.
"""
    user_prompt = f"""\
Current prompt:
{current_prompt}

Batch results:
{render_patterns(results)}

Rewrite the prompt to improve the next batch. Keep it compact.
"""
    agent = AgentConfig(
        name="prompt_improver",
        stack=improver_stack,  # type: ignore[arg-type]
        game="yc_interview",
        role="prompt_improver",
        stt="gradium",
        voice_id="",
        system_prompt=system_prompt,
    )
    reply, _ = await generate_reply(agent, user_prompt, max_tokens=900)
    return reply.strip()


def write_loop_summary(
    output_dir: Path,
    iteration_summaries: list[dict[str, Any]],
    prompt_files: list[Path],
) -> None:
    lines = ["# Hotseat Improvement Loop", "", "## Iterations", ""]
    for index, summary in enumerate(iteration_summaries, start=1):
        lines.append(f"### Iteration {index}")
        lines.append("")
        lines.append(f"- Prompt: `{prompt_files[index - 1].name}`")
        lines.append(f"- Overall average: {summary['overall_average']}/10")
        lines.append(f"- Unsupported claims: {summary['unsupported_claim_count']}")
        for metric, score in summary["metric_averages"].items():
            lines.append(f"- {metric}: {score}/10")
        lines.append("")

    if len(iteration_summaries) >= 2:
        first = iteration_summaries[0]
        last = iteration_summaries[-1]
        lines.extend(["## Change", ""])
        lines.append(
            f"- Overall: {first['overall_average']} -> {last['overall_average']} "
            f"({round(last['overall_average'] - first['overall_average'], 2):+})"
        )
        lines.append(
            f"- Unsupported claims: {first['unsupported_claim_count']} -> "
            f"{last['unsupported_claim_count']}"
        )
        lines.append("")

    (output_dir / "loop_summary.md").write_text("\n".join(lines).rstrip() + "\n")
    (output_dir / "loop_summary.json").write_text(
        json.dumps({"iterations": iteration_summaries}, indent=2) + "\n"
    )


async def main_async(args: argparse.Namespace) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path("runs") / args.game / f"loop-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles = await generate_profiles(args.profiles)
    (output_dir / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")

    prompt_files = []
    iteration_summaries = []
    current_prompt = args.seed_prompt_file.read_text().strip()

    for iteration in range(1, args.iterations + 1):
        prompt_file = output_dir / f"prompt_iter_{iteration}.md"
        prompt_file.write_text(current_prompt + "\n")
        prompt_files.append(prompt_file)

        iter_dir = output_dir / f"iteration-{iteration:02d}"
        print(f"\n=== Iteration {iteration}/{args.iterations} ===")
        results, summary = await run_parallel_batch(args, prompt_file, profiles, iter_dir)
        iteration_summaries.append(summary)

        if iteration < args.iterations:
            current_prompt = await improve_prompt(
                current_prompt=current_prompt,
                results=results,
                improver_stack=args.improver_stack,
            )

    write_loop_summary(output_dir, iteration_summaries, prompt_files)

    if args.copy_to_downloads:
        downloads_dir = Path.home() / "Downloads" / output_dir.name
        if downloads_dir.exists():
            shutil.rmtree(downloads_dir)
        shutil.copytree(output_dir, downloads_dir)
        print(f"Copied loop report to {downloads_dir}")

    print(f"Wrote loop outputs to {output_dir}")


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
