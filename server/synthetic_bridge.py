"""Synthetic voice bridge for two AI agents.

This harness avoids WebRTC/Twilio while still exercising the voice loop:

    speaker LLM text -> Gradium TTS audio -> listener STT -> listener LLM text

It is intentionally scenario-agnostic. Swap scenarios/roles via CLI flags.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import soxr
import websockets
from dotenv import load_dotenv
from openai import AsyncOpenAI

from arena_config import build_system_instruction, opening_user_message

load_dotenv(override=True)

StackName = Literal["gpt", "nemotron"]
STTName = Literal["gradium", "nvidia"]

GRADIUM_TTS_URL = "wss://api.gradium.ai/api/speech/tts"
GRADIUM_STT_URL = "wss://api.gradium.ai/api/speech/asr"
TARGET_STT_SAMPLE_RATE = 16000


@dataclass
class AgentConfig:
    name: str
    stack: StackName
    scenario: str
    role: str
    stt: STTName
    voice_id: str
    system_prompt: str
    messages: list[dict[str, str]] = field(default_factory=list)


@dataclass
class VoicePass:
    audio_bytes: int
    tts_sample_rate: int
    tts_ms: int
    stt_ms: int
    transcript: str
    wav_path: str | None
    status: str = "ok"
    error: str | None = None


def _now_ms() -> int:
    return int(time.perf_counter() * 1000)


def _clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def _pcm_resample(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return audio
    samples = np.frombuffer(audio, dtype=np.int16)
    if samples.size == 0:
        return b""
    normalized = samples.astype(np.float32) / 32768.0
    resampled = soxr.resample(normalized, source_rate, target_rate)
    clipped = np.clip(resampled * 32767.0, -32768, 32767).astype(np.int16)
    return clipped.tobytes()


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


async def synthesize_gradium(text: str, voice_id: str) -> tuple[bytes, int, int]:
    start = _now_ms()
    context_id = str(uuid.uuid4())
    headers = {"x-api-key": os.environ["GRADIUM_API_KEY"], "x-api-source": "pipecat"}
    audio_parts: list[bytes] = []
    sample_rate = 48000
    seen_audio = False
    quiet_deadline_s = 5.0

    async with websockets.connect(GRADIUM_TTS_URL, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "output_format": "pcm",
                    "voice_id": voice_id,
                    "close_ws_on_eos": False,
                    "client_req_id": context_id,
                }
            )
        )
        await ws.send(json.dumps({"type": "text", "text": text, "client_req_id": context_id}))

        while True:
            timeout = quiet_deadline_s if seen_audio else 15.0
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except TimeoutError:
                break

            msg = json.loads(raw)
            msg_type = msg.get("type")
            if msg_type == "ready":
                sample_rate = int(msg.get("sample_rate") or sample_rate)
            elif msg_type == "audio":
                audio_parts.append(base64.b64decode(msg["audio"]))
                seen_audio = True
            elif msg_type == "end_of_stream":
                break
            elif msg_type == "error":
                raise RuntimeError(f"Gradium TTS error: {msg}")

    return b"".join(audio_parts), sample_rate, _now_ms() - start


async def transcribe_gradium(pcm_16k: bytes) -> tuple[str, int]:
    start = _now_ms()
    headers = {"x-api-key": os.environ["GRADIUM_API_KEY"], "x-api-source": "pipecat"}
    transcript_parts: list[str] = []

    async with websockets.connect(GRADIUM_STT_URL, additional_headers=headers) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "model_name": "default",
                    "input_format": "pcm_16000",
                    "json_config": {"language": "en"},
                }
            )
        )
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ready.get("type") == "error":
            raise RuntimeError(f"Gradium STT setup error: {ready}")

        chunk_bytes = int(TARGET_STT_SAMPLE_RATE * 2 * 0.1)
        for offset in range(0, len(pcm_16k), chunk_bytes):
            chunk = pcm_16k[offset : offset + chunk_bytes]
            if chunk:
                await ws.send(
                    json.dumps(
                        {
                            "type": "audio",
                            "audio": base64.b64encode(chunk).decode("utf-8"),
                        }
                    )
                )
                await asyncio.sleep(0.005)

        await ws.send(json.dumps({"type": "flush", "flush_id": str(uuid.uuid4())}))

        flushed = False
        quiet_after_flush_s = 1.0
        while True:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=quiet_after_flush_s if flushed else 10.0
                )
            except TimeoutError:
                break
            msg = json.loads(raw)
            msg_type = msg.get("type")
            if msg_type == "text":
                transcript_parts.append(str(msg.get("text") or ""))
            elif msg_type == "flushed":
                flushed = True
            elif msg_type == "error":
                raise RuntimeError(f"Gradium STT error: {msg}")

    return _clean_text(" ".join(transcript_parts)), _now_ms() - start


async def transcribe_nvidia(pcm_16k: bytes) -> tuple[str, int]:
    start = _now_ms()
    url = os.getenv("NVIDIA_ASR_URL", "ws://44.241.251.184:8080")
    transcript = ""

    async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if ready.get("type") != "ready":
            raise RuntimeError(f"Unexpected NVIDIA ASR ready message: {ready}")

        chunk_bytes = int(TARGET_STT_SAMPLE_RATE * 2 * 0.1)
        for offset in range(0, len(pcm_16k), chunk_bytes):
            chunk = pcm_16k[offset : offset + chunk_bytes]
            if chunk:
                await ws.send(chunk)
                await asyncio.sleep(0.005)

        await ws.send(json.dumps({"type": "reset", "finalize": True}))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if msg.get("type") == "transcript" and msg.get("is_final"):
                transcript = str(msg.get("text") or "")
                break
            if msg.get("type") == "error":
                raise RuntimeError(f"NVIDIA ASR error: {msg}")

    return _clean_text(transcript), _now_ms() - start


async def voice_pass(
    text: str,
    speaker: AgentConfig,
    listener: AgentConfig,
    audio_dir: Path | None,
    turn_index: int,
) -> VoicePass:
    tts_pcm, tts_rate, tts_ms = await synthesize_gradium(text, speaker.voice_id)
    pcm_16k = _pcm_resample(tts_pcm, tts_rate, TARGET_STT_SAMPLE_RATE)

    wav_path = None
    if audio_dir:
        wav = audio_dir / f"turn-{turn_index:02d}-{speaker.name}-to-{listener.name}.wav"
        _write_wav(wav, pcm_16k, TARGET_STT_SAMPLE_RATE)
        wav_path = str(wav)

    if listener.stt == "nvidia":
        transcript, stt_ms = await transcribe_nvidia(pcm_16k)
    else:
        transcript, stt_ms = await transcribe_gradium(pcm_16k)

    return VoicePass(
        audio_bytes=len(pcm_16k),
        tts_sample_rate=tts_rate,
        tts_ms=tts_ms,
        stt_ms=stt_ms,
        transcript=transcript,
        wav_path=wav_path,
    )


def _remaining_timeout(deadline_ms: int | None, fallback_s: float) -> float:
    if deadline_ms is None:
        return fallback_s
    remaining_s = max((deadline_ms - _now_ms()) / 1000, 0.1)
    return min(fallback_s, remaining_s)


def _client_for_stack(stack: StackName) -> AsyncOpenAI:
    if stack == "nemotron":
        return AsyncOpenAI(
            api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
            base_url=os.getenv(
                "NEMOTRON_LLM_URL",
                "http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1",
            ),
        )
    return AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _model_for_stack(stack: StackName) -> str:
    if stack == "nemotron":
        return os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
    return os.getenv("OPENAI_MODEL", "gpt-4.1")


async def generate_reply(agent: AgentConfig, heard_text: str, max_tokens: int) -> tuple[str, int]:
    agent.messages.append({"role": "user", "content": heard_text})
    start = _now_ms()
    client = _client_for_stack(agent.stack)

    extra_body = None
    if agent.stack == "nemotron":
        enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
        extra_body = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}

    response = await client.chat.completions.create(
        model=_model_for_stack(agent.stack),
        messages=[{"role": "system", "content": agent.system_prompt}, *agent.messages],
        temperature=0.7,
        max_tokens=max_tokens,
        extra_body=extra_body,
    )
    text = _clean_text(response.choices[0].message.content or "")
    agent.messages.append({"role": "assistant", "content": text})
    return text, _now_ms() - start


def build_agent(
    *,
    name: str,
    stack: StackName,
    scenario: str,
    role: str,
    voice_id: str,
    stt: STTName | None = None,
) -> AgentConfig:
    stt = stt or ("nvidia" if stack == "nemotron" else "gradium")
    return AgentConfig(
        name=name,
        stack=stack,
        scenario=scenario,
        role=role,
        stt=stt,
        voice_id=voice_id,
        system_prompt=(
            build_system_instruction(scenario, role)
            + "\n\nTimed arena constraint: keep each turn under 25 spoken words unless the other speaker explicitly asks for a longer answer."
        ),
    )


async def run_bridge(args: argparse.Namespace) -> dict:
    started_ms = _now_ms()
    deadline_ms = (
        started_ms + int(args.time_limit_seconds * 1000) if args.time_limit_seconds else None
    )
    left = build_agent(
        name="left",
        stack=args.left_stack,
        scenario=args.left_scenario,
        role=args.left_role,
        voice_id=args.left_voice,
        stt=args.left_stt,
    )
    right = build_agent(
        name="right",
        stack=args.right_stack,
        scenario=args.right_scenario,
        role=args.right_role,
        voice_id=args.right_voice,
        stt=args.right_stt,
    )

    audio_dir = Path(args.audio_dir) if args.audio_dir else None
    current = right if args.starts == "right" else left
    other = left if current is right else right
    heard = opening_user_message(current.scenario, current.role)
    transcript = []

    print(f"Starting synthetic bridge: {left.stack}/{left.role} <-> {right.stack}/{right.role}")

    for turn_index in range(1, args.turns + 1):
        if deadline_ms is not None and _now_ms() >= deadline_ms:
            print(f"\nStopping: time limit reached before turn {turn_index}.")
            break

        turn_status = "ok"
        turn_error = None
        fallback_used = False

        try:
            reply, llm_ms = await asyncio.wait_for(
                generate_reply(current, heard, args.max_tokens),
                timeout=_remaining_timeout(deadline_ms, args.llm_timeout_seconds),
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

        spoken = _clean_text(reply)
        print(f"\n[{turn_index}] {current.name} ({current.stack}/{current.role})")
        print(f"said: {spoken}")

        try:
            vp = await asyncio.wait_for(
                voice_pass(spoken, current, other, audio_dir, turn_index),
                timeout=_remaining_timeout(deadline_ms, args.voice_timeout_seconds),
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
            "scenario": left.scenario,
            "role": left.role,
            "stt": left.stt,
        },
        "right": {
            "stack": right.stack,
            "scenario": right.scenario,
            "role": right.role,
            "stt": right.stt,
        },
        "config": {
            "time_limit_seconds": args.time_limit_seconds,
            "max_turns": args.turns,
            "max_tokens": args.max_tokens,
            "llm_timeout_seconds": args.llm_timeout_seconds,
            "voice_timeout_seconds": args.voice_timeout_seconds,
            "elapsed_ms": _now_ms() - started_ms,
        },
        "turns": transcript,
    }

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nWrote transcript: {output}")

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a synthetic voice bridge between two agents.")
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--time-limit-seconds", type=float, default=60.0)
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument("--llm-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--voice-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--starts", choices=["left", "right"], default="right")
    parser.add_argument("--output", default="runs/synthetic-bridge/latest.json")
    parser.add_argument("--audio-dir", default="runs/synthetic-bridge/audio")

    parser.add_argument("--left-stack", choices=["gpt", "nemotron"], default="nemotron")
    parser.add_argument("--left-scenario", default="yc_interview")
    parser.add_argument("--left-role", default="founder")
    parser.add_argument(
        "--left-voice", default=os.getenv("LEFT_GRADIUM_VOICE_ID") or "Eu9iL_CYe8N-Gkx_"
    )
    parser.add_argument("--left-stt", choices=["gradium", "nvidia"], default=None)

    parser.add_argument("--right-stack", choices=["gpt", "nemotron"], default="gpt")
    parser.add_argument("--right-scenario", default="yc_interview")
    parser.add_argument("--right-role", default="interviewer")
    parser.add_argument(
        "--right-voice", default=os.getenv("RIGHT_GRADIUM_VOICE_ID") or "_6Aslh2DxfmnRLmP"
    )
    parser.add_argument("--right-stt", choices=["gradium", "nvidia"], default=None)
    return parser.parse_args()


def main() -> None:
    asyncio.run(run_bridge(parse_args()))


if __name__ == "__main__":
    main()
