"""Startup profile generation and formatting helpers."""

from __future__ import annotations

import json
from typing import Any

from core.llm import generate_reply
from core.types import AgentConfig

REQUIRED_PROFILE_KEYS = [
    "company",
    "one_liner",
    "customer",
    "problem",
    "product",
    "traction",
    "pricing",
    "go_to_market",
    "weaknesses",
    "why_now",
]


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned.removeprefix("```json").strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```").strip()
    if cleaned.endswith("```"):
        cleaned = cleaned.removesuffix("```").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    return json.loads(cleaned[start : end + 1])


def format_startup_profile(profile: dict[str, Any]) -> str:
    labels = {
        "company": "Company",
        "one_liner": "One-liner",
        "customer": "Customer",
        "problem": "Problem",
        "product": "Product",
        "traction": "Traction",
        "pricing": "Pricing",
        "go_to_market": "Go-to-market",
        "weaknesses": "Weaknesses",
        "why_now": "Why now",
    }
    lines = []
    for key in REQUIRED_PROFILE_KEYS:
        value = profile.get(key)
        if value:
            lines.append(f"{labels[key]}: {value}")
    return "\n".join(lines)


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_PROFILE_KEYS if not str(profile.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Startup profile missing required keys: {', '.join(missing)}")
    return {key: str(profile[key]).strip() for key in REQUIRED_PROFILE_KEYS}


async def generate_startup_profile(
    *, idea: str | None = None, index: int = 1, attempts: int = 3
) -> dict[str, Any]:
    system_prompt = """\
You generate compact startup profiles for YC-style interview simulations.
Return only valid JSON. Do not include markdown.
Each profile should be plausible, specific, and different from generic AI SaaS.
Include concrete but synthetic facts so an interview agent has enough material.
"""
    user_prompt = f"""\
Generate startup profile #{index}.

If this seed idea is non-empty, build around it:
{idea or ""}

Return exactly these string keys:
company, one_liner, customer, problem, product, traction, pricing, go_to_market, weaknesses, why_now.
Keep each value under 35 words.
"""
    last_error = None
    for attempt in range(1, attempts + 1):
        agent = AgentConfig(
            name="startup_generator",
            stack="gemini",
            game="yc_interview",
            role="startup_generator",
            stt="gradium",
            voice_id="",
            system_prompt=system_prompt,
        )
        prompt = user_prompt
        if attempt > 1:
            prompt = (
                f"{user_prompt}\n\nPrevious attempt failed: {last_error}. "
                "Return every required key."
            )
        reply, _ = await generate_reply(agent, prompt, max_tokens=700)
        try:
            return validate_profile(extract_json_object(reply))
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
    raise ValueError(f"Could not generate valid startup profile after {attempts} attempts")


def load_profile_json(text: str) -> dict[str, Any]:
    return validate_profile(json.loads(text))
