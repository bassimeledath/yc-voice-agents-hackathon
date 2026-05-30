"""CLI for running fast text-only game simulations.

This uses the same game config, prompts, and LLM adapters as the voice bridge,
but bypasses TTS/STT so prompt iterations can run cheaply before a voice demo.
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from core.game_config import build_agent, load_game_definition, opening_for
from core.text_bridge import run_text_bridge
from core.types import BridgeOptions

load_dotenv(override=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a text-only agent game simulation.")
    parser.add_argument("--game", default="yc_interview")
    parser.add_argument("--turns", type=int, default=12)
    parser.add_argument("--time-limit-seconds", type=float, default=45.0)
    parser.add_argument("--max-tokens", type=int, default=110)
    parser.add_argument("--llm-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--starts", choices=["left", "right"], default=None)
    parser.add_argument("--output", default=None)

    parser.add_argument(
        "--candidate-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron"
    )
    parser.add_argument("--candidate-variant", default="base")

    parser.add_argument(
        "--interviewer-stack", choices=["gemini", "gpt", "nemotron"], default="nemotron"
    )
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> dict:
    game = load_game_definition(args.game)
    starts = args.starts or game.get("starts", "right")
    output = args.output or (
        f"runs/{args.game}/text-{args.candidate_stack}-{args.candidate_variant}.json"
    )

    candidate = build_agent(
        game=args.game,
        side="candidate",
        name="candidate",
        stack=args.candidate_stack,
        prompt_variant=args.candidate_variant,
    )
    interviewer = build_agent(
        game=args.game,
        side="interviewer",
        name="interviewer",
        stack=args.interviewer_stack,
    )

    opening_side = "interviewer" if starts == "right" else "candidate"
    return await run_text_bridge(
        left=candidate,
        right=interviewer,
        opening_message=opening_for(args.game, opening_side),
        options=BridgeOptions(
            turns=args.turns,
            time_limit_seconds=args.time_limit_seconds,
            max_tokens=args.max_tokens,
            llm_timeout_seconds=args.llm_timeout_seconds,
            voice_timeout_seconds=0.0,
            starts=starts,
            output=output,
            audio_dir=None,
        ),
    )


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
