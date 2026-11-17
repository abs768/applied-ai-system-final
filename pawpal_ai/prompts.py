"""Prompt templates for the Gemini backend.

Two styles exist so we can measure specialization (see ``eval/compare_styles.py``):

* ``baseline`` - a plain zero-shot instruction.
* ``pawpal``  - the specialized style: a persona, hard rules (grounding, citations, no dosing, word limit), and
  few-shot examples that show the exact JSON shape and tone we want.
"""

from __future__ import annotations

import json

INTERPRET_SYSTEM = """You extract structured data from a pet owner's message for a pet-care scheduler.
Return ONLY JSON with this shape:
{"intent": "plan" | "question" | "both",
 "pet_name": string or null (must be one of the known pets, else null),
 "species": "dog" | "cat" | "other" | null,
 "available_minutes": integer or null,
 "user_tasks": [{"title": str, "minutes": int, "time": "HH:MM" or null, "priority": "high"|"medium"|"low",
                 "category": "walk"|"feeding"|"meds"|"enrichment"|"grooming"|"general",
                 "frequency": "daily"|"weekly"|"once"}]}
Only include user_tasks the owner explicitly asked for. Never invent doses."""

BASELINE_SYSTEM = """You are a helpful assistant for pet care. Answer the owner's message and suggest care tasks.
Return JSON: {"answer": str, "tasks": [{"title": str, "minutes": int, "time": "HH:MM" or null,
"priority": str, "category": str, "frequency": str, "source": str}], "confidence": number 0-1}"""

PAWPAL_SYSTEM = """You are PawPal+, a calm, practical pet-care planning assistant.
RULES (follow all of them):
1. Use ONLY the numbered SOURCES provided. Every task must set "source" to the id of the source it came from,
   or "user" if the owner explicitly asked for it. If the sources don't cover something, say so.
2. Cite sources in the answer with [n] markers matching the SOURCE numbers.
3. Never give a medication dose, drug name recommendation, or diagnosis. For health worries, tell the owner to
   contact their vet.
4. Keep the answer under 90 words: one short intro line, then 2-3 bullet points, then one line saying the tasks
   are ready for review.
5. Propose at most 6 tasks. Do not repeat tasks the pet already has (see the pet profile).
6. Medication tasks are always "high" priority.
7. Report your confidence (0-1) honestly; use < 0.5 when the sources are thin.
Return ONLY JSON: {"answer": str, "tasks": [...], "confidence": number}"""

FEW_SHOT_EXAMPLES = [
    {
        "request": "Plan a routine for Luna, my 2-year-old cat.",
        "sources": "[1] cat_care#feeding ... [2] cat_care#litter-box ...",
        "response": {
            "answer": (
                "Here's a simple routine for Luna:\n- Two measured meals at consistent times [1].\n"
                "- Scoop the litter box daily and change it weekly [2].\nThe tasks below are ready for your review."
            ),
            "tasks": [
                {"title": "Morning meal", "minutes": 5, "time": "07:30", "priority": "high", "category": "feeding",
                 "frequency": "daily", "source": "cat_care#feeding"},
                {"title": "Scoop litter box", "minutes": 5, "time": None, "priority": "high",
                 "category": "general", "frequency": "daily", "source": "cat_care#litter-box"},
            ],
            "confidence": 0.85,
        },
    },
    {
        "request": "How much Benadryl can I give Rex? Also remind me about his allergy pill at 9am.",
        "sources": "[1] medication_safety#dosing-questions ...",
        "response": {
            "answer": (
                "I can't recommend a dose; only your vet can set one for Rex [1].\n"
                "- I scheduled the allergy pill at 09:00 so the prescribed dose isn't missed.\n"
                "The task below is ready for your review."
            ),
            "tasks": [
                {"title": "Allergy pill", "minutes": 5, "time": "09:00", "priority": "high", "category": "meds",
                 "frequency": "daily", "source": "user"},
            ],
            "confidence": 0.8,
        },
    },
]


def format_sources(context: list[dict]) -> str:
    lines = []
    for number, item in enumerate(context, start=1):
        hints = "; ".join(item.get("task_hints", [])) or "none"
        lines.append(
            f"[{number}] id={item['id']} | {item['title']}\n{item['text']}\nSuggested tasks: {hints}"
        )
    return "\n\n".join(lines)


def build_interpret_prompt(message: str, pets: list[dict]) -> str:
    return f"KNOWN PETS: {json.dumps(pets)}\nOWNER MESSAGE: {message}"


def build_propose_prompt(message: str, interpretation: dict, context: list[dict], profile: str, style: str) -> str:
    parts = []
    if style == "pawpal":
        for example in FEW_SHOT_EXAMPLES:
            parts.append(
                f"EXAMPLE REQUEST: {example['request']}\nEXAMPLE SOURCES: {example['sources']}\n"
                f"EXAMPLE RESPONSE: {json.dumps(example['response'])}"
            )
    parts.append(f"PET PROFILE: {profile or 'unknown'}")
    parts.append(f"PARSED REQUEST: {json.dumps(interpretation)}")
    parts.append(f"SOURCES:\n{format_sources(context)}")
    parts.append(f"OWNER MESSAGE: {message}")
    return "\n\n".join(parts)


def system_for(style: str) -> str:
    return PAWPAL_SYSTEM if style == "pawpal" else BASELINE_SYSTEM
