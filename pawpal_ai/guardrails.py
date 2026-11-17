"""Input and output guardrails for the PawPal+ AI Care Assistant.

Input guardrails run *before* any model call:
    * empty / oversized input is rejected
    * prompt-injection attempts are blocked
    * possible emergencies short-circuit planning and point the owner to a vet
    * dosing questions are flagged so the assistant refuses to give a dose (but can still schedule the reminder)

Output guardrails run *after* the model proposes tasks (see ``verify_and_repair`` in ``agent.py``):
    * every task is validated against the scheduler's schema and repaired or dropped
    * medication tasks are forced to high priority
    * tasks must cite a retrieved source (grounding) or be explicitly requested by the user
    * duplicate tasks are removed
    * dose amounts are redacted from the answer text
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_INPUT_CHARS = 800
VALID_PRIORITIES = {"high", "medium", "low"}
VALID_CATEGORIES = {"walk", "feeding", "meds", "enrichment", "grooming", "general"}
VALID_FREQUENCIES = {"daily", "weekly", "once"}
MIN_TASK_MINUTES, MAX_TASK_MINUTES = 1, 240

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)",
    r"disregard (all |the )?(previous|prior|above|your) (instructions|rules)",
    r"(reveal|print|show) (me )?(your|the) (system )?prompt",
    r"you are now (a|an|in) ",
    r"\bjailbreak\b",
    r"developer mode",
]

TOXIN_WORDS = (
    "chocolate|grape|grapes|raisin|raisins|onion|onions|garlic|xylitol|lily|lilies|ibuprofen|acetaminophen|"
    "tylenol|advil|paracetamol|antifreeze|rat poison|poison|alcohol|macadamia"
)
EMERGENCY_PATTERNS = [
    rf"\b(ate|eaten|eat|swallowed|chewed|licked|got into)\b.{{0,40}}\b({TOXIN_WORDS})\b",
    r"\b(seizure|seizing|convuls\w*)\b",
    r"\b(not breathing|can'?t breathe|cannot breathe|struggling to breathe|choking)\b",
    r"\b(collapsed|unconscious|unresponsive|passed out)\b",
    r"\b(bleeding (a lot|heavily|badly|won'?t stop)|blood everywhere)\b",
    r"\b(hit by a car|bloat(ed)?|swollen belly)\b",
    r"\bstraining to (pee|urinate)\b",
    r"\b(poisoned|poisoning)\b",
]

DOSING_PATTERNS = [
    r"\bhow (much|many)\b.{0,40}\b(mg|ml|milligrams?|tablets?|pills?|units?|dose|medicine|medication|ibuprofen|"
    r"tylenol|benadryl|aspirin|insulin)\b",
    r"\b(dose|dosage|dosing)\b",
    r"\b(can|should) i give\b.{0,30}\b(ibuprofen|tylenol|acetaminophen|aspirin|advil|benadryl|paracetamol|human)\b",
]

DOSE_AMOUNT_PATTERN = re.compile(
    r"[^.\n]*\b\d+(\.\d+)?\s?(mg|ml|milligrams?|tablets?|units?|cc|mg/kg)\b[^.\n]*\.?", re.IGNORECASE
)

PET_VOCAB = re.compile(
    r"\b(pet|pets|dog|dogs|puppy|pup|cat|cats|kitten|rabbit|bunny|bird|hamster|guinea|walk|feed|food|meal|"
    r"litter|groom|brush|meds?|medication|pill|insulin|vet|play|toy|treat|leash|kibble|routine|schedule|plan|"
    r"care|water|nails|teeth)\w*",
    re.IGNORECASE,
)

EMERGENCY_MESSAGE = (
    "This sounds like it could be an emergency. Please contact your veterinarian or an emergency animal "
    "hospital right now. In the US you can also call the ASPCA Animal Poison Control Center (888-426-4435) or "
    "Pet Poison Helpline (855-764-7661). PawPal+ does not diagnose or treat, so it has paused normal planning."
)
DOSING_MESSAGE = (
    "I can't recommend a dose or a medication. Doses depend on your pet's weight, health and the exact product, "
    "so please ask your veterinarian. I can still schedule the reminder so the dose your vet prescribed is never "
    "missed."
)


@dataclass
class InputCheck:
    status: str  # "ok" | "rejected" | "blocked" | "emergency" | "off_topic"
    message: str = ""
    flags: list[str] = field(default_factory=list)
    cleaned: str = ""

    @property
    def can_continue(self) -> bool:
        return self.status == "ok"


def check_input(text: str | None, pet_names: list[str] | None = None) -> InputCheck:
    """Run all input guardrails and return a decision."""
    cleaned = (text or "").strip()
    if not cleaned:
        return InputCheck("rejected", "Please type a request, for example: 'Plan a routine for Biscuit'.")
    if len(cleaned) > MAX_INPUT_CHARS:
        return InputCheck(
            "rejected", f"That request is {len(cleaned)} characters; please keep it under {MAX_INPUT_CHARS}."
        )
    lowered = cleaned.lower()
    if any(re.search(pattern, lowered) for pattern in INJECTION_PATTERNS):
        return InputCheck(
            "blocked",
            "That request tries to change how PawPal+ works, so it was blocked. Ask about pet care instead.",
            ["prompt_injection"],
            cleaned,
        )
    if any(re.search(pattern, lowered) for pattern in EMERGENCY_PATTERNS):
        return InputCheck("emergency", EMERGENCY_MESSAGE, ["emergency"], cleaned)

    flags: list[str] = []
    if any(re.search(pattern, lowered) for pattern in DOSING_PATTERNS):
        flags.append("dosing_request")

    mentions_pet = any(name and name.lower() in lowered for name in (pet_names or []))
    if not mentions_pet and not PET_VOCAB.search(cleaned):
        return InputCheck(
            "off_topic",
            "PawPal+ only helps with pet care routines and questions. Try: 'What should Biscuit's day look like?'",
            ["off_topic"],
            cleaned,
        )
    return InputCheck("ok", "", flags, cleaned)


def valid_hhmm(text: str | None) -> bool:
    if not isinstance(text, str) or not re.fullmatch(r"\d{2}:\d{2}", text):
        return False
    hours, minutes = map(int, text.split(":"))
    return 0 <= hours < 24 and 0 <= minutes < 60


def validate_task(raw: dict) -> tuple[dict | None, list[str]]:
    """Validate and repair one proposed task dict. Returns (task or None, list of issues found)."""
    issues: list[str] = []
    title = str(raw.get("title", "")).strip()
    if not title:
        return None, ["dropped a task with no title"]
    if len(title) > 60:
        issues.append(f"shortened long title '{title[:20]}...'")
        title = title[:60].rstrip()

    try:
        minutes = int(raw.get("minutes", raw.get("duration_minutes", 15)))
    except (TypeError, ValueError):
        issues.append(f"'{title}': invalid duration, defaulted to 15 min")
        minutes = 15
    if not MIN_TASK_MINUTES <= minutes <= MAX_TASK_MINUTES:
        clamped = max(MIN_TASK_MINUTES, min(MAX_TASK_MINUTES, minutes))
        issues.append(f"'{title}': duration {minutes} min clamped to {clamped}")
        minutes = clamped

    priority = str(raw.get("priority", "medium")).lower()
    if priority not in VALID_PRIORITIES:
        issues.append(f"'{title}': unknown priority '{priority}', set to medium")
        priority = "medium"

    category = str(raw.get("category", "general")).lower()
    if category not in VALID_CATEGORIES:
        issues.append(f"'{title}': unknown category '{category}', set to general")
        category = "general"

    if category == "meds" and priority != "high":
        issues.append(f"'{title}': medication raised to high priority")
        priority = "high"

    time = raw.get("time", raw.get("preferred_time"))
    time = time.strip() if isinstance(time, str) and time.strip() else None
    if time is not None and not valid_hhmm(time):
        issues.append(f"'{title}': invalid time '{time}' removed (task becomes flexible)")
        time = None

    frequency = str(raw.get("frequency", "daily")).lower()
    if frequency not in VALID_FREQUENCIES:
        issues.append(f"'{title}': unknown frequency '{frequency}', set to daily")
        frequency = "daily"

    source = str(raw.get("source", "")).strip()
    return (
        {
            "title": title,
            "minutes": minutes,
            "priority": priority,
            "category": category,
            "time": time,
            "frequency": frequency,
            "source": source,
        },
        issues,
    )


def redact_dosing(text: str) -> tuple[str, bool]:
    """Remove sentences containing dose amounts (e.g. '5 mg', '2 tablets'). Returns (text, redacted?)."""
    redacted = DOSE_AMOUNT_PATTERN.sub(" [dose removed - ask your vet]", text)
    return re.sub(r"\s{2,}", " ", redacted).strip(), redacted != text
