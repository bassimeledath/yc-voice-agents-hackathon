"""Manager scripts for Kitchen Rush evaluations."""

from __future__ import annotations

from typing import Any

KITCHEN_RUSH_MANAGER_SCRIPTS: list[dict[str, Any]] = [
    {
        "name": "lunch-rush",
        "opening": "Lunch rush. Work each ticket as it fires and keep me updated.",
        "questions": {
            4: "How long on T1 soup?",
            8: "What's done right now?",
        },
    },
    {
        "name": "burger-pressure",
        "opening": "Tickets are coming fast. Prioritize what I call out, but do not serve anything sloppy.",
        "questions": {
            3: "Can you serve T2 burger as soon as possible?",
            7: "Give me a quick status.",
        },
    },
    {
        "name": "status-interrupt",
        "opening": "New lunch shift. Multiple tickets may overlap, so multitask cleanly.",
        "questions": {
            2: "What are you starting with?",
            6: "Wait, what is already plated?",
        },
    },
    {
        "name": "quiet-shift",
        "opening": "Quiet comms today. Keep the station moving without waiting for me.",
        "questions": {},
    },
    {
        "name": "expediter-noise",
        "opening": "I'm watching the pass. Call out progress, but stay on the oldest live ticket.",
        "questions": {
            1: "What are you touching first?",
            5: "Anything ready to serve?",
            10: "Which ticket is most at risk?",
        },
    },
    {
        "name": "soup-watch",
        "opening": "Soup timing matters today. Do not guess; use the latest kitchen state.",
        "questions": {
            3: "Is any soup still cooking?",
            6: "Can you use that wait time?",
            11: "What is left before service?",
        },
    },
    {
        "name": "ticket-recap",
        "opening": "I need crisp ticket recaps while you work. No stalling on status.",
        "questions": {
            2: "Recap the active ticket.",
            7: "What is plated right now?",
            12: "What is not served yet?",
        },
    },
    {
        "name": "late-rush",
        "opening": "Assume every second matters. Finish ready food before starting nice-to-have prep.",
        "questions": {
            4: "Are you switching tickets or finishing the current one?",
            9: "What should leave the kitchen next?",
        },
    },
    {
        "name": "manager-pressure",
        "opening": "I'm going to pressure you, but do not take invalid shortcuts.",
        "questions": {
            3: "Can you serve T2 before T1 is done?",
            6: "Can you plate anything that is still cooking?",
            10: "What is the safe next move?",
        },
    },
]
