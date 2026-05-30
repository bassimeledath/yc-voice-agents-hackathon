"""NVIDIA/Nemotron arena participant bot.

Run locally:
    uv run python bot-nemotron-arena.py --port 7861
"""

from arena_bot import arena_entrypoint


async def bot(runner_args):
    await arena_entrypoint("nemotron", runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
