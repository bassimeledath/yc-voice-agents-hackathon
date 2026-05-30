"""Audio, TTS, and STT helpers for synthetic voice runs."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
import wave
from pathlib import Path

import numpy as np
import soxr
import websockets

from core.types import AgentConfig, VoicePass

GRADIUM_TTS_URL = "wss://api.gradium.ai/api/speech/tts"
GRADIUM_STT_URL = "wss://api.gradium.ai/api/speech/asr"
TARGET_STT_SAMPLE_RATE = 16000


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())


def pcm_resample(audio: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return audio
    samples = np.frombuffer(audio, dtype=np.int16)
    if samples.size == 0:
        return b""
    normalized = samples.astype(np.float32) / 32768.0
    resampled = soxr.resample(normalized, source_rate, target_rate)
    clipped = np.clip(resampled * 32767.0, -32768, 32767).astype(np.int16)
    return clipped.tobytes()


def write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


async def synthesize_gradium(text: str, voice_id: str) -> tuple[bytes, int, int]:
    start = now_ms()
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

    return b"".join(audio_parts), sample_rate, now_ms() - start


async def transcribe_gradium(pcm_16k: bytes) -> tuple[str, int]:
    start = now_ms()
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

    return clean_text(" ".join(transcript_parts)), now_ms() - start


async def transcribe_nvidia(pcm_16k: bytes) -> tuple[str, int]:
    start = now_ms()
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

    return clean_text(transcript), now_ms() - start


async def voice_pass(
    text: str,
    speaker: AgentConfig,
    listener: AgentConfig,
    audio_dir: Path | None,
    turn_index: int,
) -> VoicePass:
    tts_pcm, tts_rate, tts_ms = await synthesize_gradium(text, speaker.voice_id)
    pcm_16k = pcm_resample(tts_pcm, tts_rate, TARGET_STT_SAMPLE_RATE)

    wav_path = None
    if audio_dir:
        wav = audio_dir / f"turn-{turn_index:02d}-{speaker.name}-to-{listener.name}.wav"
        write_wav(wav, pcm_16k, TARGET_STT_SAMPLE_RATE)
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
