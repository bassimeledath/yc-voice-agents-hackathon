"""Reusable synthetic voice conversation loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.audio import clean_text, now_ms, voice_pass
from core.llm import generate_reply
from core.types import AgentConfig, BridgeOptions, VoicePass


def remaining_timeout(deadline_ms: int | None, fallback_s: float) -> float:
    if deadline_ms is None:
        return fallback_s
    remaining_s = max((deadline_ms - now_ms()) / 1000, 0.1)
    return min(fallback_s, remaining_s)


async def run_bridge(
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
    audio_dir = Path(options.audio_dir) if options.audio_dir else None
    current = right if options.starts == "right" else left
    other = left if current is right else right
    heard = opening_message
    transcript = []

    print(f"Starting synthetic bridge: {left.stack}/{left.role} <-> {right.stack}/{right.role}")

    for turn_index in range(1, options.turns + 1):
        if deadline_ms is not None and now_ms() >= deadline_ms:
            print(f"\nStopping: time limit reached before turn {turn_index}.")
            break

        turn_status = "ok"
        turn_error = None
        fallback_used = False

        try:
            reply, llm_ms = await asyncio.wait_for(
                generate_reply(current, heard, options.max_tokens),
                timeout=remaining_timeout(deadline_ms, options.llm_timeout_seconds),
            )
        except Exception as exc:
            turn_status = "llm_error"
            turn_error = f"{type(exc).__name__}: {exc}"
            transcript.append(
                {
                    "turn": turn_index,
                    "speaker": current.name,
                    "speaker_stack": current.stack,
                    "speaker_role": current.role,
                    "listener": other.name,
                    "listener_stack": other.stack,
                    "listener_role": other.role,
                    "status": turn_status,
                    "error": turn_error,
                    "spoken_text": "",
                    "heard_text": "",
                    "fallback_used": False,
                    "metrics_ms": {"llm": None, "tts": None, "stt": None},
                }
            )
            print(f"\n[{turn_index}] {current.name} failed during LLM: {turn_error}")
            break

        spoken = clean_text(reply)
        print(f"\n[{turn_index}] {current.name} ({current.stack}/{current.role})")
        print(f"said: {spoken}")

        try:
            vp = await asyncio.wait_for(
                voice_pass(spoken, current, other, audio_dir, turn_index),
                timeout=remaining_timeout(deadline_ms, options.voice_timeout_seconds),
            )
        except Exception as exc:
            vp = VoicePass(
                audio_bytes=0,
                tts_sample_rate=0,
                tts_ms=0,
                stt_ms=0,
                transcript="",
                wav_path=None,
                status="voice_error",
                error=f"{type(exc).__name__}: {exc}",
            )

        if not vp.transcript:
            fallback_used = True
            turn_status = vp.status if vp.status != "ok" else "empty_transcript"
            heard_next = spoken
        else:
            heard_next = vp.transcript

        if vp.error and not turn_error:
            turn_error = vp.error

        print(
            f"heard by {other.name} via {other.stt}: {vp.transcript} "
            f"(status={turn_status}, llm={llm_ms}ms tts={vp.tts_ms}ms stt={vp.stt_ms}ms)"
        )

        transcript.append(
            {
                "turn": turn_index,
                "speaker": current.name,
                "speaker_stack": current.stack,
                "speaker_role": current.role,
                "listener": other.name,
                "listener_stack": other.stack,
                "listener_role": other.role,
                "status": turn_status,
                "error": turn_error,
                "spoken_text": spoken,
                "heard_text": vp.transcript,
                "fallback_used": fallback_used,
                "fallback_text": spoken if fallback_used else None,
                "metrics_ms": {"llm": llm_ms, "tts": vp.tts_ms, "stt": vp.stt_ms},
                "audio_bytes_16k_pcm": vp.audio_bytes,
                "tts_sample_rate": vp.tts_sample_rate,
                "wav_path": vp.wav_path,
            }
        )

        heard = heard_next
        current, other = other, current

    result = {
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
            "voice_timeout_seconds": options.voice_timeout_seconds,
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
