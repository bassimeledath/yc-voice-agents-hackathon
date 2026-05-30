"""Load game and agent prompts from filesystem config."""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.defaults import base_system_prompt
from core.types import AgentConfig, StackName, STTName

SERVER_ROOT = Path(__file__).resolve().parents[1]


def _read_text(base_dir: Path, relative_path: str) -> str:
    return (base_dir / relative_path).read_text().strip()


def _voice_id(explicit: str | None, env_name: str, default: str) -> str:
    return explicit or os.getenv(env_name) or default


def load_game_definition(game: str) -> dict:
    game_dir = SERVER_ROOT / "games" / game
    path = game_dir / "game.json"
    if not path.exists():
        raise FileNotFoundError(f"Unknown game {game!r}: {path} does not exist")
    data = json.loads(path.read_text())
    data["_game_dir"] = str(game_dir)
    return data


def build_role_prompt(game: str, side: str, variant: str = "base") -> str:
    data = load_game_definition(game)
    game_dir = Path(data["_game_dir"])
    role_config = data[side]

    prompt_path = role_config.get("prompt")
    if side == "candidate":
        variants = role_config.get("prompt_variants", {})
        prompt_path = variants.get(variant) or prompt_path
        if not prompt_path:
            raise ValueError(f"Game {game!r} has no candidate prompt variant {variant!r}")

    return base_system_prompt(
        title=role_config["title"],
        role_context=_read_text(game_dir, prompt_path),
        behavior=role_config["behavior"],
    )


def build_role_prompt_from_text(game: str, side: str, role_context: str) -> str:
    data = load_game_definition(game)
    role_config = data[side]
    return base_system_prompt(
        title=role_config["title"],
        role_context=role_context,
        behavior=role_config["behavior"],
    )


def with_runtime_context(system_prompt: str, *, title: str, context: str) -> str:
    if not context.strip():
        return system_prompt
    return f"{system_prompt}\n\nRuntime {title}:\n{context.strip()}"


def opening_for(game: str, side: str) -> str:
    data = load_game_definition(game)
    return data[side]["opening"]


def default_stt_for(stack: StackName) -> STTName:
    return "nvidia" if stack == "nemotron" else "gradium"


def build_agent(
    *,
    game: str,
    side: str,
    name: str,
    stack: StackName,
    prompt_variant: str = "base",
    stt: STTName | None = None,
    voice_id: str | None = None,
    runtime_context_title: str | None = None,
    runtime_context: str | None = None,
    prompt_text_override: str | None = None,
) -> AgentConfig:
    data = load_game_definition(game)
    role_config = data[side]
    defaults = data.get("defaults", {})
    voice_defaults = defaults.get("voices", {})

    voice_stack = "gemini" if stack == "gpt" else stack
    default_voice = voice_defaults.get(voice_stack) or (
        "Eu9iL_CYe8N-Gkx_" if stack == "nemotron" else "_6Aslh2DxfmnRLmP"
    )
    env_name = f"{name.upper()}_GRADIUM_VOICE_ID"

    if prompt_text_override is None:
        system_prompt = build_role_prompt(game, side, prompt_variant)
    else:
        system_prompt = build_role_prompt_from_text(game, side, prompt_text_override)
    if runtime_context:
        system_prompt = with_runtime_context(
            system_prompt,
            title=runtime_context_title or "context",
            context=runtime_context,
        )

    return AgentConfig(
        name=name,
        stack=stack,
        game=game,
        role=role_config["role"],
        stt=stt or default_stt_for(stack),
        voice_id=_voice_id(voice_id, env_name, default_voice),
        system_prompt=system_prompt,
    )
