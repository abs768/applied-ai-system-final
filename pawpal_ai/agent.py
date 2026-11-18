"""The PawPal+ AI Care Agent: plan -> retrieve -> propose -> act (schedule) -> verify -> repair -> explain.

The agent never edits the owner's real data. It works on a copy, and the caller (CLI or Streamlit) applies the
proposed tasks only after a human approves them (``apply_tasks``). Every step is recorded in ``AgentResult.trace``
and can be saved to ``logs/traces/`` as JSON + Markdown.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from pawpal_ai.guardrails import DOSING_MESSAGE, check_input, redact_dosing, validate_task
from pawpal_ai.llm import BROAD_REQUEST, LLMClient, OfflineClient
from pawpal_ai.retrieval import KnowledgeBase, MultiSourceRetriever, RetrievedChunk
from pawpal_system import Owner, Pet, ScheduledTask, Scheduler, Task, to_hhmm, to_minutes

logger = logging.getLogger("pawpal_ai")
TRACE_DIR = Path(__file__).resolve().parent.parent / "logs" / "traces"
STYLE_WORD_LIMIT = 110


@dataclass
class AgentResult:
    status: str  # ok | clarify | emergency | blocked | rejected | off_topic
    answer: str
    pet_name: str | None = None
    tasks: list[dict] = field(default_factory=list)
    plan: list[ScheduledTask] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    confidence_label: str = "low"
    needs_review: bool = True
    backend: str = "offline"
    style: str = "pawpal"
    message: str = ""
    trace: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["plan"] = [
            {"time": f"{s.start_time}-{s.end_time}", "pet": s.pet_name, "task": s.task.title,
             "minutes": s.task.duration_minutes, "priority": s.task.priority, "why": s.reason}
            for s in self.plan
        ]
        return data


def age_terms(pet: Pet | None) -> str:
    """Add life-stage words to the retrieval query (e.g. a 9-year-old dog -> 'senior')."""
    if pet is None or pet.age <= 0:  # age 0 means "not entered" in the PawPal+ UI
        return ""
    if pet.species == "dog" and pet.age >= 7:
        return "senior older"
    if pet.species == "cat" and pet.age >= 10:
        return "senior older"
    return ""


ROUTINE_FACETS = {
    "dog": ["dog feeding meals water", "dog exercise walks", "dog enrichment training grooming"],
    "cat": ["cat feeding meals water", "cat litter box", "cat play enrichment grooming"],
    "other": ["rabbit diet hay feeding", "rabbit exercise free-roam", "cage habitat cleaning"],
}


def life_stage_ok(chunk_id: str, pet: Pet | None) -> bool:
    """Drop guide sections for the wrong life stage (e.g. senior advice for a 4-year-old cat)."""
    if pet is None or pet.age <= 0:
        return True
    senior = bool(age_terms(pet))
    if "senior" in chunk_id and not senior:
        return False
    if ("puppies" in chunk_id or "kitten" in chunk_id) and pet.age >= 1:
        return False
    return True


class CareAgent:
    def __init__(
        self,
        owner: Owner,
        llm: LLMClient | None = None,
        knowledge_base: KnowledgeBase | None = None,
        style: str = "pawpal",
        day: date | None = None,
        max_repairs: int = 2,
        use_retrieval: bool = True,
    ) -> None:
        self.owner = owner
        self.llm = llm or OfflineClient()
        self.kb = knowledge_base or KnowledgeBase()
        self.style = style
        self.day = day or date.today()
        self.max_repairs = max_repairs
        self.use_retrieval = use_retrieval
        self._trace: list[dict] = []
        self._t0 = 0.0

    # ------------------------------------------------------------------ tracing
    def _log(self, step: str, detail: str, data: object | None = None) -> None:
        entry = {"n": len(self._trace) + 1, "step": step, "detail": detail,
                 "ms": round((time.perf_counter() - self._t0) * 1000, 1)}
        if data is not None:
            entry["data"] = data
        self._trace.append(entry)
        logger.info("[%s] %s", step, detail)

    def _finish(self, result: AgentResult) -> AgentResult:
        result.backend = self.llm.name
        result.style = self.style
        for event in getattr(self.llm, "events", []):
            self._log("fallback", event)
        self._log("done", f"status={result.status}, confidence={result.confidence:.2f} ({result.confidence_label})")
        result.trace = self._trace
        return result

    # ------------------------------------------------------------------ main loop
    def run(self, message: str) -> AgentResult:
        self._trace, self._t0 = [], time.perf_counter()
        if hasattr(self.llm, "events"):
            self.llm.events.clear()
        pet_names = [pet.name for pet in self.owner.pets]

        # 1. Input guardrail
        check = check_input(message, pet_names)
        self._log("guardrail_input", f"status={check.status}, flags={check.flags}")
        if check.status != "ok":
            result = AgentResult(status=check.status, answer=check.message, message=message or "",
                                 needs_review=check.status == "emergency")
            if check.status == "emergency":
                # Still retrieve the emergency guide so the owner sees grounded, cited advice.
                hits = self.kb.search(check.cleaned + " emergency vet toxic", k=2)
                result.sources = [self._source_dict(hit) for hit in hits]
                self._log("retrieve", "emergency guide retrieved", [s["id"] for s in result.sources])
            return self._finish(result)

        # 2. Plan: interpret the request
        pets_payload = [{"name": p.name, "species": p.species, "age": p.age, "breed": p.breed} for p in self.owner.pets]
        interpretation = self.llm.interpret(check.cleaned, pets_payload)
        interpretation = self._validate_interpretation(interpretation, check.cleaned)
        self._log("plan", f"intent={interpretation['intent']}, pet={interpretation['pet_name']}, "
                          f"explicit tasks={len(interpretation['user_tasks'])}", interpretation)

        pet = self.owner.get_pet(interpretation["pet_name"]) if interpretation["pet_name"] else None
        if interpretation["intent"] in {"plan", "both"} and pet is None:
            if not self.owner.pets:
                msg = "Add a pet first (name and species), then ask me to plan their care."
            else:
                msg = f"Which pet is this for? You have: {', '.join(pet_names)}."
            self._log("clarify", msg)
            return self._finish(AgentResult(status="clarify", answer=msg, message=message))

        species = pet.species if pet else interpretation.get("species")

        # 3. Retrieve (RAG over knowledge base + pet profile)
        extra = [age_terms(pet), pet.breed if pet else ""] if interpretation["intent"] != "question" else []
        query = " ".join(filter(None, [check.cleaned, species or "", *extra]))
        hits: list[RetrievedChunk] = []
        if self.use_retrieval:
            retriever = MultiSourceRetriever(self.kb, self.owner)
            hits = retriever.retrieve(query, pet.name if pet else None, species, k=6)
            broad = interpretation["intent"] in {"plan", "both"} and bool(BROAD_REQUEST.search(check.cleaned))
            if broad and pet is not None:
                # Planning step: a broad "routine" request is decomposed into one sub-query per care facet so the
                # plan covers feeding, exercise and hygiene instead of only the single best-matching section.
                facets = ROUTINE_FACETS.get(pet.species, ROUTINE_FACETS["other"])
                if age_terms(pet):  # seniors: senior guidance replaces the generic exercise facet
                    facets = [f"senior {pet.species} care"] + [f for f in facets if "exercise" not in f]
                self._log("plan", f"broad request decomposed into {len(facets)} sub-queries", facets)
                facet_hits = [h for facet in facets for h in self.kb.search(facet, species=species, k=2)[:1]]
                seen, merged = set(), []
                for hit in [h for h in hits if h.chunk.source == "pet_profile"] + facet_hits + hits:
                    if hit.chunk.chunk_id not in seen:
                        seen.add(hit.chunk.chunk_id)
                        merged.append(hit)
                hits = merged
            dropped = [h.chunk.chunk_id for h in hits if not life_stage_ok(h.chunk.chunk_id, pet)]
            hits = [h for h in hits if life_stage_ok(h.chunk.chunk_id, pet)]
            if dropped:
                self._log("retrieve", f"life-stage filter removed {dropped}")
            profile_hits = [h for h in hits if h.chunk.source == "pet_profile"]
            hits = profile_hits + [h for h in hits if h.chunk.source != "pet_profile"][:4]
        context = [self._context_dict(hit) for hit in hits]
        knowledge = [item for item in context if item["source"] == "knowledge"]
        profile = next((item["text"] for item in context if item["source"] == "pet_profile"), "")
        self._log("retrieve", f"query='{query}' -> {len(knowledge)} guide section(s), profile={'yes' if profile else 'no'}",
                  [{"id": item["id"], "score": item["score"]} for item in context])

        # 4. Propose (model call grounded in the retrieved context)
        proposal = self.llm.propose(check.cleaned, interpretation, context, profile, self.style)
        raw_tasks = proposal.get("tasks", []) if isinstance(proposal.get("tasks"), list) else []
        self._log("propose", f"{len(raw_tasks)} task(s) proposed, model confidence={proposal.get('confidence')}",
                  {"answer": proposal.get("answer", ""), "tasks": raw_tasks})

        # 5-6. Act + verify + repair loop
        allowed_sources = {item["id"] for item in knowledge} | {"user"}
        tasks, repairs, warnings = self._verify_tasks(raw_tasks, pet, allowed_sources)
        minutes = interpretation.get("available_minutes") or self.owner.available_minutes
        plan, conflicts, skipped = self._schedule(pet, tasks, minutes) if tasks else ([], [], [])
        for attempt in range(1, self.max_repairs + 1):
            fixed = self._repair_conflicts(tasks, plan) or self._repair_budget(tasks, plan, minutes)
            if not fixed:
                break
            repairs.extend(fixed)
            self._log("repair", f"attempt {attempt}: {'; '.join(fixed)}")
            plan, conflicts, skipped = self._schedule(pet, tasks, minutes)
        if conflicts:
            warnings.append(f"{len(conflicts)} conflict(s) involve times you chose; please adjust them yourself.")
        if any("high priority" in s for s in skipped):
            warnings.append("A high-priority task did not fit in today's time budget.")
        self._log("verify", f"{len(tasks)} valid task(s), {len(repairs)} repair(s), {len(conflicts)} conflict(s), "
                            f"{len(skipped)} skipped", {"repairs": repairs, "warnings": warnings})

        # 7. Explain: output guardrails + specialized style
        answer = str(proposal.get("answer", "")).strip()
        if "dosing_request" in check.flags:
            answer = f"{DOSING_MESSAGE}\n{answer}"
            warnings.append("Dosing question detected: no dose given; owner told to ask a vet.")
        answer, redacted = redact_dosing(answer)
        if redacted:
            warnings.append("A dose amount was removed from the answer.")
        if self.style == "pawpal":
            answer = self._enforce_style(answer, knowledge)
        self._log("guardrail_output", f"dose redacted={redacted}, words={len(answer.split())}")

        confidence = self._confidence(knowledge, raw_tasks, tasks, repairs, warnings, proposal.get("confidence"))
        label = "high" if confidence >= 0.75 else "medium" if confidence >= 0.5 else "low"
        result = AgentResult(
            status="ok", answer=answer, pet_name=pet.name if pet else None, tasks=tasks, plan=plan,
            conflicts=conflicts, skipped=skipped, warnings=warnings, repairs=repairs,
            sources=[self._source_dict_from_context(item) for item in knowledge],
            confidence=confidence, confidence_label=label,
            needs_review=bool(tasks) or label == "low", message=message,
        )
        return self._finish(result)

    # ------------------------------------------------------------------ helpers
    def _validate_interpretation(self, data: dict, message: str) -> dict:
        """Guard against a model inventing pets or returning junk in the interpret step."""
        intent = data.get("intent") if data.get("intent") in {"plan", "question", "both"} else "plan"
        pet_name = data.get("pet_name")
        real = self.owner.get_pet(pet_name) if isinstance(pet_name, str) else None
        if real is None:
            real = next((p for p in self.owner.pets if p.name.lower() == str(pet_name).lower()), None)
        if real is None and len(self.owner.pets) == 1 and intent in {"plan", "both"}:
            real = self.owner.pets[0]
        minutes = data.get("available_minutes")
        minutes = minutes if isinstance(minutes, int) and 0 < minutes <= 1440 else None
        user_tasks = [task for task in data.get("user_tasks", []) if isinstance(task, dict)]
        for task in user_tasks:
            task["source"] = "user"
        return {"intent": intent, "pet_name": real.name if real else None, "species": data.get("species"),
                "available_minutes": minutes, "user_tasks": user_tasks}

    def _verify_tasks(self, raw_tasks: list, pet: Pet | None, allowed_sources: set[str]):
        tasks, repairs, warnings = [], [], []
        existing = {t.title.lower() for t in pet.get_pending_tasks()} if pet else set()
        seen: set[str] = set()
        for raw in raw_tasks:
            if not isinstance(raw, dict):
                repairs.append("dropped a malformed task")
                continue
            task, issues = validate_task(raw)
            repairs.extend(issues)
            if task is None:
                continue
            if task["category"] == "meds" and task["source"] != "user":
                repairs.append(f"dropped '{task['title']}': medication schedules must come from the owner/vet, "
                               "not the AI")
                continue
            if task["source"] not in allowed_sources:
                repairs.append(f"dropped '{task['title']}': not grounded in a retrieved source")
                continue
            key = task["title"].lower()
            if key in existing or key in seen:
                repairs.append(f"dropped duplicate '{task['title']}'")
                continue
            seen.add(key)
            tasks.append(task)
        if pet is None and tasks:
            warnings.append("No pet selected, so tasks were not scheduled.")
        return tasks, repairs, warnings

    def _schedule(self, pet: Pet | None, tasks: list[dict], minutes: int):
        """Tool call: run the existing PawPal+ Scheduler on a copy of the owner with the proposed tasks added."""
        if pet is None:
            return [], [], []
        sandbox = copy.deepcopy(self.owner)
        sandbox.available_minutes = minutes
        target = sandbox.get_pet(pet.name)
        for task in tasks:
            target.add_task(Task(title=task["title"], duration_minutes=task["minutes"], priority=task["priority"],
                                 category=task["category"], preferred_time=task["time"],
                                 frequency=task["frequency"], due_date=self.day))
        scheduler = Scheduler(sandbox, day=self.day)
        plan = scheduler.generate_plan()
        conflicts = scheduler.detect_conflicts(plan)
        skipped = [f"'{t.title}' for {p.name} ({t.priority} priority): {reason}" for p, t, reason in scheduler.skipped]
        self._log("act", f"Scheduler.generate_plan() -> {len(plan)} slot(s), {len(conflicts)} conflict(s), "
                         f"{len(skipped)} skipped (budget {minutes} min)")
        return plan, conflicts, skipped

    def _repair_conflicts(self, tasks: list[dict], plan: list[ScheduledTask]) -> list[str]:
        """Self-correction: free the lower-priority *AI-proposed* fixed-time task in each overlap."""
        rank = {"high": 3, "medium": 2, "low": 1}
        proposed = {t["title"]: t for t in tasks if t["source"] != "user" and t["time"]}
        ordered = sorted(plan, key=lambda s: to_minutes(s.start_time))
        fixes = []
        for i, first in enumerate(ordered):
            for second in ordered[i + 1:]:
                if to_minutes(second.start_time) >= to_minutes(first.end_time):
                    break
                candidates = [s for s in (first, second) if s.task.title in proposed and proposed[s.task.title]["time"]]
                if not candidates:
                    continue
                loser = min(candidates, key=lambda s: (rank.get(s.task.priority, 0), -to_minutes(s.start_time)))
                other = second if loser is first else first
                task = proposed[loser.task.title]
                new_start = to_minutes(other.end_time)
                if new_start + task["minutes"] <= 24 * 60:
                    new_time = to_hhmm(new_start)
                    fixes.append(f"moved '{task['title']}' from {task['time']} to {new_time} "
                                 f"(right after '{other.task.title}')")
                    task["time"] = new_time
                else:
                    fixes.append(f"made '{task['title']}' flexible (overlapped '{other.task.title}' near midnight)")
                    task["time"] = None
                return fixes  # re-plan after each move so later overlaps are judged on the new schedule
        return fixes

    def _repair_budget(self, tasks: list[dict], plan: list[ScheduledTask], minutes: int) -> list[str]:
        """Self-correction: if an owner-requested task didn't fit the time budget, shorten it to fit (min 10 min)."""
        planned = {slot.task.title for slot in plan}
        left = minutes - sum(slot.task.duration_minutes for slot in plan)
        for task in tasks:
            if task["source"] == "user" and task["title"] not in planned and 10 <= left < task["minutes"]:
                old_minutes, task["minutes"] = task["minutes"], left
                return [f"shortened '{task['title']}' from {old_minutes} to {left} min to fit your time budget"]
        return []

    def _enforce_style(self, answer: str, knowledge: list[dict]) -> str:
        """Specialized PawPal style: cited, short, structured."""
        if knowledge and not re.search(r"\[\d+\]", answer):
            answer += " [1]"
        if len(answer.split()) > STYLE_WORD_LIMIT:
            lines = answer.splitlines()
            head, middle, tail = lines[:1], lines[1:-1], lines[-1:]
            while middle and len(" ".join(head + middle + tail).split()) > STYLE_WORD_LIMIT:
                middle.pop()
            answer = "\n".join(head + middle + tail)
        return answer

    def _confidence(self, knowledge, raw_tasks, tasks, repairs, warnings, model_conf) -> float:
        retrieval = min(1.0, (knowledge[0]["score"] / 0.4)) if knowledge else 0.0
        kept = len(tasks) / len(raw_tasks) if raw_tasks else 1.0
        penalty = min(0.3, 0.1 * len(warnings))
        score = 0.5 * retrieval + 0.3 * kept + 0.2 - penalty
        if isinstance(model_conf, (int, float)) and 0 <= model_conf <= 1:
            score = 0.8 * score + 0.2 * float(model_conf)
        return round(max(0.0, min(1.0, score)), 2)

    @staticmethod
    def _context_dict(hit) -> dict:
        chunk = hit.chunk
        return {
            "id": chunk.chunk_id, "title": chunk.title, "text": chunk.text, "score": hit.score,
            "source": chunk.source, "species": chunk.species, "hints": chunk.task_hints,
            "task_hints": [f"{h.title} ({h.minutes} min, {h.priority}, {h.time or 'any time'}, {h.frequency})"
                           for h in chunk.task_hints],
        }

    @staticmethod
    def _source_dict(hit) -> dict:
        return {"id": hit.chunk.chunk_id, "title": hit.chunk.title, "score": hit.score}

    @staticmethod
    def _source_dict_from_context(item: dict) -> dict:
        return {"id": item["id"], "title": item["title"], "score": item["score"]}


# ---------------------------------------------------------------------- human-in-the-loop + persistence
def apply_tasks(owner: Owner, result: AgentResult, titles: list[str] | None = None, day: date | None = None) -> int:
    """Add approved tasks to the real pet. Only called after a human approves."""
    pet = owner.get_pet(result.pet_name) if result.pet_name else None
    if pet is None or result.status != "ok":
        return 0
    added = 0
    for task in result.tasks:
        if titles is not None and task["title"] not in titles:
            continue
        pet.add_task(Task(title=task["title"], duration_minutes=task["minutes"], priority=task["priority"],
                          category=task["category"], preferred_time=task["time"], frequency=task["frequency"],
                          due_date=day or date.today()))
        added += 1
    return added


def save_trace(result: AgentResult, directory: Path = TRACE_DIR, name: str | None = None) -> Path:
    """Save the full reasoning trace as JSON plus a readable Markdown version. Returns the Markdown path."""
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", result.message.lower()).strip("-")[:40] or result.status
    stamp = name or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{slug}"
    data = result.to_dict()
    (directory / f"{stamp}.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    md_path = directory / f"{stamp}.md"
    md_path.write_text(render_trace_markdown(result), encoding="utf-8")
    return md_path


def render_trace_markdown(result: AgentResult) -> str:
    lines = [
        f"### Request: \"{result.message}\"",
        "",
        f"- **Backend:** {result.backend} | **Style:** {result.style} | **Status:** {result.status} | "
        f"**Confidence:** {result.confidence:.2f} ({result.confidence_label})",
        "",
        "| # | Step | What happened |",
        "|---|------|---------------|",
    ]
    for entry in result.trace:
        lines.append(f"| {entry['n']} | `{entry['step']}` | {entry['detail'].replace('|', '/')} |")
    lines += ["", "**Final answer:**", "", "```text", result.answer, "```"]
    if result.tasks:
        lines += ["", "**Proposed tasks (awaiting human approval):**", "", "| Task | Min | Priority | Time | Source |",
                  "|------|-----|----------|------|--------|"]
        lines += [f"| {t['title']} | {t['minutes']} | {t['priority']} | {t['time'] or 'flexible'} | {t['source']} |"
                  for t in result.tasks]
    if result.repairs:
        lines += ["", "**Self-checks and repairs:**", ""] + [f"- {item}" for item in result.repairs]
    if result.warnings:
        lines += ["", "**Warnings:**", ""] + [f"- {item}" for item in result.warnings]
    return "\n".join(lines) + "\n"
