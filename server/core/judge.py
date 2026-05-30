"""YC-style transcript judging helpers."""

from __future__ import annotations

import json
from typing import Any

from core.llm import generate_reply
from core.startups import extract_json_object, format_startup_profile
from core.types import AgentConfig

METRICS = [
    "clear_matter_of_fact_explanation",
    "users_metrics_and_learning",
    "candid_obstacles_and_specific_insight",
]

STRICT_RUBRIC = """\
Use this strict 0-10 scale:
- 9-10: exceptional YC-style answer; crisp, specific, direct, and no material unsupported claims.
- 7-8: good answer, but incomplete, lightly generic, or has only minor unsupported details.
- 5-6: plausible but materially incomplete, evasive, generic, or contains important unsupported claims.
- 3-4: weak, confusing, mostly generic, or repeatedly unsupported.
- 0-2: fails to answer, contradicts the profile, or is not founder-like.

Hard caps:
- If any high-severity unsupported claim appears, cap overall_score at 6.5.
- If two or more medium-severity unsupported claims appear, cap overall_score at 7.0.
- If a metric contains an unsupported claim about revenue, customers, retention, CAC, conversion, renewal, expansion, pilots, LOIs, named customers, or product capabilities, cap that metric at 6.5.
- If the answer sounds polished but relies on invented specifics, score the style fairly but enforce the caps.
"""


def transcript_for_judge(run: dict[str, Any]) -> str:
    lines = []
    for turn in run.get("turns", []):
        speaker = turn.get("speaker_role") or turn.get("speaker") or "unknown"
        text = turn.get("spoken_text") or ""
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


async def judge_transcript(
    *,
    startup_profile: dict[str, Any],
    run: dict[str, Any],
    judge_stack: str = "gemini",
) -> dict[str, Any]:
    system_prompt = """\
You are a strict, fair YC-style interview judge.
Use YC-style preferences: clear and matter-of-fact explanations, strong user/metric learning, candid discussion of obstacles, and specific founder insight.
Score independently. Do not compare to other agents.
The founder may make plausible stylistic extrapolations, but you must flag material unsupported claims separately when they introduce new numbers, users, traction, quotes, channels, or product capabilities not present in the startup profile.
Apply the hard caps. Do not give 9+ scores to transcripts with material unsupported claims.
Return only valid JSON.
"""
    user_prompt = f"""\
Startup profile:
{format_startup_profile(startup_profile)}

Transcript:
{transcript_for_judge(run)}

{STRICT_RUBRIC}

Return JSON with this shape:
{{
  "scores": {{
    "clear_matter_of_fact_explanation": {{"score": 0-10, "reason": "short reason"}},
    "users_metrics_and_learning": {{"score": 0-10, "reason": "short reason"}},
    "candid_obstacles_and_specific_insight": {{"score": 0-10, "reason": "short reason"}}
  }},
  "unsupported_claims": [
    {{"claim": "short quote or paraphrase", "why_unsupported": "short reason", "severity": "low|medium|high"}}
  ],
  "overall_score": 0-10,
  "summary": "2 sentence strict summary"
}}
"""
    agent = AgentConfig(
        name="yc_judge",
        stack=judge_stack,  # type: ignore[arg-type]
        game="yc_interview",
        role="judge",
        stt="gradium",
        voice_id="",
        system_prompt=system_prompt,
    )
    reply, _ = await generate_reply(agent, user_prompt, max_tokens=1200)
    result = extract_json_object(reply)
    return normalize_judgment(result)


def normalize_judgment(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores", {})
    numeric_scores = []
    for metric in METRICS:
        item = scores.get(metric) or {}
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        item["score"] = max(0.0, min(10.0, score))
        item["reason"] = str(item.get("reason", "")).strip()
        scores[metric] = item
        numeric_scores.append(item["score"])

    result["scores"] = scores
    result["unsupported_claims"] = list(result.get("unsupported_claims") or [])
    if "overall_score" not in result:
        result["overall_score"] = round(sum(numeric_scores) / len(numeric_scores), 2)
    else:
        result["overall_score"] = max(0.0, min(10.0, float(result["overall_score"])))
    result["summary"] = str(result.get("summary", "")).strip()
    return result


def judgment_to_json(judgment: dict[str, Any]) -> str:
    return json.dumps(judgment, indent=2, sort_keys=True)
