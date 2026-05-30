"""Gemini arena participant bot.

Run locally:
    uv run python bot-gemini-arena.py --port 7860
"""

from arena_bot import arena_entrypoint


async def bot(runner_args):
    await arena_entrypoint("gemini", runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
