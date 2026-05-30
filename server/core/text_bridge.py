"""Reusable text-only conversation loop for fast agent simulations."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.audio import clean_text, now_ms
from core.bridge import remaining_timeout
from core.llm import generate_reply
from core.types import AgentConfig, BridgeOptions


async def run_text_bridge(
    *,
    left: AgentConfig,
    right: AgentConfig,
    opening_message: str,
    options: BridgeOptions,
) -> dict:
    started_ms = now_ms()
    deadline_ms = (
        started_ms + int(options.time_limit_seconds * 1000) if options.time_limit_seconds else None
    )
    current = right if options.starts == "right" else left
    other = left if current is right else right
    heard = opening_message
    transcript = []

    print(f"Starting text bridge: {left.stack}/{left.role} <-> {right.stack}/{right.role}")

    for turn_index in range(1, options.turns + 1):
        if deadline_ms is not None and now_ms() >= deadline_ms:
            print(f"\nStopping: time limit reached before turn {turn_index}.")
            break

        try:
            reply, llm_ms = await asyncio.wait_for(
                generate_reply(current, heard, options.max_tokens),
                timeout=remaining_timeout(deadline_ms, options.llm_timeout_seconds),
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            transcript.append(
                {
                    "turn": turn_index,
                    "speaker": current.name,
                    "speaker_stack": current.stack,
                    "speaker_role": current.role,
                    "listener": other.name,
                    "listener_stack": other.stack,
                    "listener_role": other.role,
                    "status": "llm_error",
                    "error": error,
                    "spoken_text": "",
                    "heard_text": "",
                    "metrics_ms": {"llm": None},
                }
            )
            print(f"\n[{turn_index}] {current.name} failed during LLM: {error}")
            break

        spoken = clean_text(reply)
        print(f"\n[{turn_index}] {current.name} ({current.stack}/{current.role})")
        print(f"said: {spoken} (llm={llm_ms}ms)")

        transcript.append(
            {
                "turn": turn_index,
                "speaker": current.name,
                "speaker_stack": current.stack,
                "speaker_role": current.role,
                "listener": other.name,
                "listener_stack": other.stack,
                "listener_role": other.role,
                "status": "ok",
                "error": None,
                "spoken_text": spoken,
                "heard_text": spoken,
                "metrics_ms": {"llm": llm_ms},
            }
        )

        heard = spoken
        current, other = other, current

    result = {
        "mode": "text",
        "left": {
            "stack": left.stack,
            "game": left.game,
            "role": left.role,
            "stt": left.stt,
        },
        "right": {
            "stack": right.stack,
            "game": right.game,
            "role": right.role,
            "stt": right.stt,
        },
        "config": {
            "time_limit_seconds": options.time_limit_seconds,
            "max_turns": options.turns,
            "max_tokens": options.max_tokens,
            "llm_timeout_seconds": options.llm_timeout_seconds,
            "elapsed_ms": now_ms() - started_ms,
        },
        "turns": transcript,
    }

    if options.output:
        output = Path(options.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nWrote transcript: {output}")

    return result
