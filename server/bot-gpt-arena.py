"""GPT-4.1 arena participant bot.

Run locally:
    uv run python bot-gpt-arena.py --port 7860
"""

from arena_bot import arena_entrypoint


async def bot(runner_args):
    await arena_entrypoint("gpt", runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
