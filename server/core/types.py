"""Shared types for the voice-agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StackName = Literal["gemini", "gpt", "nemotron"]
STTName = Literal["gradium", "nvidia"]


@dataclass
class AgentConfig:
    name: str
    stack: StackName
    game: str
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


@dataclass
class BridgeOptions:
    turns: int
    time_limit_seconds: float | None
    max_tokens: int
    llm_timeout_seconds: float
    voice_timeout_seconds: float
    starts: Literal["left", "right"]
    output: str | None
    audio_dir: str | None
