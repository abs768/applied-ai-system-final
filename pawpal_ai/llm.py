"""Model backends for the PawPal+ AI Care Assistant.

Every backend implements the same two calls the agent needs:

* ``interpret(message, pets)`` -> structured request (intent, pet, explicit tasks, time budget)
* ``propose(message, interpretation, context, profile, style)`` -> answer text + tasks citing sources

``GeminiClient`` calls Google Gemini (set ``GEMINI_API_KEY``). ``OfflineClient`` is a deterministic, rule-based
stand-in that uses the *same retrieved context* so the whole pipeline (RAG, agent loop, guardrails, evaluation) is
reproducible with no API key or network. If a Gemini call fails or returns invalid JSON, the Gemini client retries
once and then falls back to the offline client for that step, and the fallback is recorded in the agent trace.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

from pawpal_ai import prompts
from pawpal_ai.retrieval import tokenize

logger = logging.getLogger("pawpal_ai")

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
BROAD_REQUEST = re.compile(
    r"\b(routine|everything|day look like|whole day|full day|daily care|care plan|schedule for|organi[sz]e)\b", re.I
)
QUESTION_START = re.compile(r"^\s*(how|what|when|why|should|can|could|is|are|do|does|which|who)\b", re.I)
PLAN_WORDS = re.compile(r"\b(plan|schedule|routine|add|remind|reminder|set up|organi[sz]e|book|put)\b", re.I)

CATEGORY_KEYWORDS = [
    ("meds", r"insulin|pills?|meds|medication|medicine|tablets?|eye drops|ear drops|drops|antibiotics?|preventive"),
    ("walk", r"walks?|walking|potty|toilet|hike|run"),
    ("feeding", r"feed|feeding|breakfast|dinner|lunch|meals?|food|hay|water"),
    ("grooming", r"brush(ing)?|groom(ing)?|bath|nails?|teeth|comb"),
    ("enrichment", r"play|training|train|puzzle|toy|fetch|enrichment"),
    ("general", r"litter|clean(ing)?|vet (appointment|visit|call)|cage|enclosure"),
]
TITLE_FOR_KEYWORD = {
    "insulin": "Give insulin", "pill": "Give pill", "pills": "Give pill", "meds": "Give medication",
    "medication": "Give medication", "medicine": "Give medication", "tablet": "Give tablet",
    "eye drops": "Eye drops", "ear drops": "Ear drops", "antibiotic": "Give antibiotics",
    "antibiotics": "Give antibiotics", "preventive": "Parasite preventive",
    "walk": "Walk", "walks": "Walk", "potty": "Potty break", "toilet": "Toilet break", "hike": "Hike", "run": "Run",
    "breakfast": "Breakfast", "dinner": "Dinner", "lunch": "Lunch", "feed": "Feeding", "meal": "Meal",
    "food": "Feeding", "hay": "Refill hay", "water": "Refresh water",
    "brush": "Brush coat", "bath": "Bath", "nails": "Trim nails", "teeth": "Brush teeth", "groom": "Grooming",
    "play": "Play session", "training": "Training session", "train": "Training session",
    "puzzle": "Puzzle feeder", "fetch": "Fetch", "litter": "Scoop litter box", "vet": "Vet appointment",
    "cage": "Clean cage", "enclosure": "Clean enclosure",
}
SPECIES_WORDS = {
    "dog": "dog", "dogs": "dog", "puppy": "dog", "pup": "dog", "cat": "cat", "cats": "cat", "kitten": "cat",
    "rabbit": "other", "bunny": "other", "bird": "other", "hamster": "other", "guinea": "other",
}


class LLMClient(Protocol):
    name: str

    def interpret(self, message: str, pets: list[dict]) -> dict: ...

    def propose(self, message: str, interpretation: dict, context: list[dict], profile: str, style: str) -> dict: ...


# ---------------------------------------------------------------- offline (deterministic) backend


def _parse_times(text: str) -> list[str]:
    """Find times like '8am', '7:30 pm', '19:30' and return HH:MM strings in order."""
    times: list[tuple[int, str]] = []
    for match in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", text, re.I):
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        meridiem = match.group(3).lower()[0]
        if hour == 12:
            hour = 0
        if meridiem == "p":
            hour += 12
        if 0 <= hour < 24 and 0 <= minute < 60:
            times.append((match.start(), f"{hour:02d}:{minute:02d}"))
    for match in re.finditer(r"\b([01]?\d|2[0-3]):([0-5]\d)\b(?!\s*(am|pm))", text, re.I):
        times.append((match.start(), f"{int(match.group(1)):02d}:{match.group(2)}"))
    return [value for _, value in sorted(times)]


def _available_minutes(text: str) -> int | None:
    match = re.search(
        r"\b(?:i have|i've got|got|only have|have only|available|free)\D{0,15}?(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)",
        text,
        re.I,
    )
    if not match:
        return None
    amount = float(match.group(1))
    return int(amount * 60) if match.group(2).lower().startswith("h") else int(amount)


class OfflineClient:
    """Deterministic rule-based backend: same inputs always give the same outputs."""

    name = "offline"

    def interpret(self, message: str, pets: list[dict]) -> dict:
        lowered = message.lower()
        pet_name = next((pet["name"] for pet in pets if re.search(rf"\b{re.escape(pet['name'].lower())}\b", lowered)), None)
        species = next((SPECIES_WORDS[word] for word in re.findall(r"[a-z]+", lowered) if word in SPECIES_WORDS), None)
        is_question = "?" in message or bool(QUESTION_START.match(message))
        wants_plan = bool(PLAN_WORDS.search(message))
        user_tasks = self._extract_tasks(message, [pet["name"] for pet in pets])
        if is_question and (wants_plan or user_tasks):
            intent = "both"
        elif is_question:
            intent = "question"
        else:
            intent = "plan"
        return {
            "intent": intent,
            "pet_name": pet_name,
            "species": species,
            "available_minutes": _available_minutes(message),
            "user_tasks": user_tasks,
        }

    def _extract_tasks(self, message: str, pet_names: list[str]) -> list[dict]:
        tasks: list[dict] = []
        verbs = r"(?:walk|feed|brush|play|give|clean|scoop|groom|train|bath|trim|refill)"
        clauses = re.split(
            rf"[.;!?\n]|,|\bthen\b|\band\b(?=\s+(?:\w+\s+)?{verbs})|\b(?:and|but)\b(?=\s+i\b)", message, flags=re.I
        )
        for clause in clauses:
            clause_l = clause.lower()
            if not clause_l.strip() or re.search(r"\b(i have|available|free time|only have)\b", clause_l):
                continue
            if QUESTION_START.match(clause) and not re.search(r"\b(remind|add|schedule)\b", clause_l):
                continue
            for category, pattern in CATEGORY_KEYWORDS:
                keyword = re.search(rf"\b({pattern})\b", clause_l)
                if keyword:
                    break
            else:
                continue
            word = keyword.group(1)
            title = TITLE_FOR_KEYWORD.get(word, TITLE_FOR_KEYWORD.get(word.rstrip("s"), word.capitalize()))
            times = _parse_times(clause)
            minutes_match = re.search(r"(\d+)\s*(?:-\s*)?(min|mins|minutes|minute)\b", clause_l)
            hours_match = re.search(r"(\d+(?:\.\d+)?)\s*(hour|hours|hr|hrs)\b", clause_l)
            minutes = (
                int(minutes_match.group(1)) if minutes_match
                else int(float(hours_match.group(1)) * 60) if hours_match
                else {"meds": 5, "feeding": 10, "walk": 30, "grooming": 15, "enrichment": 15}.get(category, 15)
            )
            twice = re.search(r"\b(twice a day|twice daily|two times a day|every 12 hours|morning and evening|bid)\b", clause_l)
            if not times and twice:
                times = ["08:00", "20:00"]
            frequency = (
                "weekly" if re.search(r"\b(weekly|once a week|every week)\b", clause_l)
                else "once" if re.search(r"\b(just today|only today|one[- ]time|once)\b", clause_l) and not twice
                else "daily"
            )
            priority = (
                "high" if category in {"meds", "feeding", "walk"} or re.search(r"\b(must|important|never miss|critical)\b", clause_l)
                else "medium"
            )
            for index, time in enumerate(times or [None]):
                suffix = ""
                if len(times) > 1:
                    hour = int(time[:2])
                    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
                    suffix = f" ({part})" if sum(1 for t in times if (int(t[:2]) < 12) == (hour < 12)) == 1 else f" ({time})"
                tasks.append(
                    {
                        "title": f"{title}{suffix}",
                        "minutes": minutes,
                        "time": time,
                        "priority": priority,
                        "category": category,
                        "frequency": frequency,
                        "source": "user",
                    }
                )
        return tasks

    def propose(self, message: str, interpretation: dict, context: list[dict], profile: str, style: str) -> dict:
        knowledge = [item for item in context if item["source"] == "knowledge"]
        query_tokens = set(tokenize(message, expand=True))
        broad = bool(BROAD_REQUEST.search(message))
        user_tasks = list(interpretation.get("user_tasks", []))
        tasks: list[dict] = []

        if interpretation.get("intent") in {"plan", "both"}:
            for rank, item in enumerate(knowledge[:3]):
                if item["score"] < 0.12:
                    continue
                for hint in item["hints"]:
                    hint_tokens = set(tokenize(f"{hint.title} {hint.category}", expand=True))
                    if not (broad or hint_tokens & query_tokens):
                        continue
                    covered = any(
                        user["category"] == hint.category
                        and (user["time"] is None or hint.time is None
                             or abs(_to_min(user["time"]) - _to_min(hint.time)) <= 90)
                        for user in user_tasks
                    )
                    if covered:
                        continue
                    tasks.append(
                        {
                            "title": hint.title, "minutes": hint.minutes, "time": hint.time,
                            "priority": hint.priority, "category": hint.category,
                            "frequency": hint.frequency, "source": item["id"],
                        }
                    )
            tasks = user_tasks + tasks[: max(0, 6 - len(user_tasks))]

        answer = self._answer(message, interpretation, knowledge, query_tokens, style, len(tasks))
        top = knowledge[0]["score"] if knowledge else 0.0
        return {"answer": answer, "tasks": tasks, "confidence": round(min(0.95, 0.4 + top), 2)}

    def _answer(self, message, interpretation, knowledge, query_tokens, style, n_tasks) -> str:
        pet = interpretation.get("pet_name") or "your pet"
        if not knowledge:
            return f"I couldn't find anything in PawPal's care guides about that for {pet}, so please check with your vet."
        if style == "baseline":
            # Baseline: no specialization - dump the raw retrieved passages, no citations, no structure, no limit.
            return " ".join(item["text"].replace("\n", " ") for item in knowledge[:2])

        bullets = []
        for number, item in enumerate(knowledge[:3], start=1):
            if number > 1 and item["score"] < 0.15:
                continue
            sentences = [s for s in re.split(r"(?<=[.!?])\s+", item["text"].replace("\n", " ")) if s.strip()]
            overlaps = [len(set(tokenize(sentence, expand=True)) & query_tokens) for sentence in sentences]
            best_overlap = max(overlaps)
            if number > 1 and best_overlap == 0:
                continue
            best = sentences[overlaps.index(best_overlap)] if best_overlap >= 2 else sentences[0]
            words = best.split()
            if len(words) > 28:
                best = " ".join(words[:28]).rstrip(",;:") + " ..."
            bullets.append(f"- {best.strip()} [{number}]")
        intro = f"Here's what PawPal's care guides say for {pet}:"
        closing = (
            "The proposed tasks below are ready for your review."
            if n_tasks else "Ask me to plan it if you'd like tasks added."
        )
        return "\n".join([intro, *bullets[:3], closing])


def _to_min(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


# ---------------------------------------------------------------- Gemini backend


def _extract_json(text: str) -> dict:
    """Parse JSON from a model reply, tolerating ```json fences."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("model did not return a JSON object")
    return data


class GeminiClient:
    """Google Gemini backend with retry + offline fallback."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None) -> None:
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self.fallback = OfflineClient()
        self.events: list[str] = []
        if client is not None:  # injected fake client for tests
            self._client = client
            return
        from google import genai  # imported lazily so offline mode needs no extra package

        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])

    def _call(self, system: str, prompt: str) -> dict:
        from google.genai import types

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system, response_mime_type="application/json", temperature=0.2
                    ),
                )
                return _extract_json(response.text)
            except Exception as error:  # network, quota, safety block, bad JSON
                last_error = error
                logger.warning("Gemini call failed (attempt %d): %s", attempt + 1, error)
        raise RuntimeError(f"Gemini failed twice: {last_error}")

    def interpret(self, message: str, pets: list[dict]) -> dict:
        try:
            data = self._call(prompts.INTERPRET_SYSTEM, prompts.build_interpret_prompt(message, pets))
            data.setdefault("user_tasks", [])
            for task in data["user_tasks"]:
                task["source"] = "user"
            return data
        except RuntimeError as error:
            self.events.append(f"interpret fell back to offline: {error}")
            return self.fallback.interpret(message, pets)

    def propose(self, message: str, interpretation: dict, context: list[dict], profile: str, style: str) -> dict:
        model_context = [item for item in context if item["source"] == "knowledge"]
        try:
            return self._call(
                prompts.system_for(style),
                prompts.build_propose_prompt(message, interpretation, model_context, profile, style),
            )
        except RuntimeError as error:
            self.events.append(f"propose fell back to offline: {error}")
            return self.fallback.propose(message, interpretation, context, profile, style)


def make_client(backend: str | None = None) -> LLMClient:
    """Pick a backend: explicit argument, then PAWPAL_LLM env var, then Gemini if a key is set, else offline."""
    choice = (backend or os.getenv("PAWPAL_LLM") or "auto").lower()
    if choice == "offline":
        return OfflineClient()
    if choice in {"gemini", "auto"} and os.getenv("GEMINI_API_KEY"):
        try:
            return GeminiClient()
        except ImportError:
            logger.warning("google-genai is not installed; using the offline backend.")
    if choice == "gemini":
        raise RuntimeError("PAWPAL_LLM=gemini but GEMINI_API_KEY is not set.")
    return OfflineClient()
