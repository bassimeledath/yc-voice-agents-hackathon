"""Run text-only YC interview transcript batches without judging."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from batch_eval import load_profiles, profiles_for_args
from core.game_config import build_agent, load_game_definition
from core.startups import format_startup_profile
from core.text_bridge import run_text_bridge
from core.types import BridgeOptions

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run transcripts without automatic judging.")
    parser.add_argument("--game", default="yc_interview")
    parser.add_argument("--profiles", type=int, default=5)
    parser.add_argument("--profile-file", type=Path, default=None)
    parser.add_argument("--startup-idea", action="append", default=[])
    parser.add_argument("--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron")
    parser.add_argument("--candidate-prompt-file", type=Path, required=True)
    parser.add_argument("--interviewer-stack", choices=["gemini", "gpt", "nemotron"], default="gemini")
    parser.add_argument("--turns", type=int, default=8)
    parser.add_argument("--time-limit-seconds", type=float, default=45.0)
    parser.add_argument("--max-tokens", type=int, default=130)
    parser.add_argument("--llm-timeout-seconds", type=float, default=25.0)
    parser.add_argument("--parallelism", type=int, default=3)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--copy-to-downloads", action="store_true")
    return parser.parse_args()


async def load_or_generate_profiles(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.profile_file:
        return load_profiles(args.profile_file)[: args.profiles]
    return await profiles_for_args(args)


async def run_one(args: argparse.Namespace, profile: dict[str, str], index: int) -> dict:
    game = load_game_definition(args.game)
    starts = game.get("starts", "right")
    prompt_text = args.candidate_prompt_file.read_text().strip()
    candidate = build_agent(
        game=args.game,
        side="candidate",
        name="candidate",
        stack=args.candidate_stack,
        prompt_text_override=prompt_text,
        runtime_context_title="startup profile",
        runtime_context=format_startup_profile(profile),
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
    return run


async def main_async(args: argparse.Namespace) -> None:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir or Path("runs") / args.game / f"transcripts-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    profiles = await load_or_generate_profiles(args)
    (output_dir / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")
    shutil.copyfile(args.candidate_prompt_file, output_dir / "candidate_prompt.md")

    semaphore = asyncio.Semaphore(args.parallelism)

    async def guarded(index: int, profile: dict[str, str]) -> dict:
        async with semaphore:
            print(f"Running transcript {index}/{len(profiles)}: {profile['company']}")
            return await run_one(args, profile, index)

    runs = await asyncio.gather(
        *(guarded(index, profile) for index, profile in enumerate(profiles, start=1))
    )

    for index, run in enumerate(runs, start=1):
        run_dir = output_dir / f"run-{index:03d}"
        run_dir.mkdir()
        (run_dir / "transcript.json").write_text(json.dumps(run, indent=2) + "\n")

    if args.copy_to_downloads:
        downloads_dir = Path.home() / "Downloads" / output_dir.name
        if downloads_dir.exists():
            shutil.rmtree(downloads_dir)
        shutil.copytree(output_dir, downloads_dir)
        print(f"Copied transcripts to {downloads_dir}")

    print(f"Wrote transcripts to {output_dir}")


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
