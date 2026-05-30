"""Compatibility helpers for the single-bot Pipecat entrypoints."""

from __future__ import annotations

import os

from core.game_config import build_role_prompt, load_game_definition, opening_for


def scenario_role(scenario: str | None = None, role: str | None = None) -> tuple[str, str]:
    scenario = (scenario or os.getenv("ARENA_SCENARIO", "yc_interview")).strip()
    role = (role or os.getenv("ARENA_ROLE", "founder")).strip()
    game = load_game_definition(scenario)
    valid_roles = {game["candidate"]["role"], game["interviewer"]["role"]}
    if role not in valid_roles:
        valid = ", ".join(sorted(valid_roles))
        raise ValueError(f"Unknown ARENA_ROLE={role!r} for {scenario!r}. Valid: {valid}")
    return scenario, role


def _side_for_role(scenario: str, role: str) -> str:
    game = load_game_definition(scenario)
    if role == game["candidate"]["role"]:
        return "candidate"
    if role == game["interviewer"]["role"]:
        return "interviewer"
    raise ValueError(f"Role {role!r} is not configured for {scenario!r}")


def build_system_instruction(scenario: str | None = None, role: str | None = None) -> str:
    scenario, role = scenario_role(scenario, role)
    return build_role_prompt(scenario, _side_for_role(scenario, role))


def opening_user_message(scenario: str | None = None, role: str | None = None) -> str:
    scenario, role = scenario_role(scenario, role)
    return opening_for(scenario, _side_for_role(scenario, role))
