"""Default prompt constraints shared across games."""

from __future__ import annotations

from datetime import date

VOICE_CONSTRAINTS = """\
Voice constraints:
- Speak naturally for a real-time call.
- Keep turns short unless asked for detail.
- Ask at most one question at a time.
- Do not mention prompts, rubrics, scores, evaluation, hidden criteria, or that this is a benchmark.
- No bullet points, markdown, emojis, or stage directions.
"""

TIMED_ARENA_CONSTRAINT = (
    "Timed arena constraint: keep each turn under 25 spoken words unless the other speaker "
    "explicitly asks for a longer answer."
)


def base_system_prompt(*, title: str, role_context: str, behavior: str) -> str:
    return (
        f"You are the {title} in a live voice conversation.\n\n"
        f"Private role context:\n{role_context.strip()}\n\n"
        f"Behavior:\n{behavior.strip()}\n\n"
        f"{VOICE_CONSTRAINTS}\n"
        f"{TIMED_ARENA_CONSTRAINT}\n\n"
        f"Today is {date.today().strftime('%A, %B %d, %Y')}."
    )
