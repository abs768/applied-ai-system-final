"""Evaluation harness for the PawPal+ AI Care Assistant.

Runs every case in eval/cases.json against a fresh demo household, checks the expectations, and prints a
pass/fail table plus a summary (pass rate, average confidence, grounding rate, guardrail catches).

    python -m eval.run_eval                     # full system (RAG + specialized style), offline backend
    python -m eval.run_eval --backend gemini    # same cases against Gemini (needs GEMINI_API_KEY)
    python -m eval.run_eval --compare           # full vs no-RAG vs baseline style, writes eval/comparison.md
    python -m eval.run_eval --save              # also write eval/results.md and eval/results.json
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from pawpal_ai.agent import AgentResult, CareAgent
from pawpal_ai.llm import make_client
from pawpal_ai.retrieval import KnowledgeBase
from pawpal_system import Owner, Pet, Task

EVAL_DIR = Path(__file__).resolve().parent
DAY = date(2026, 11, 18)  # fixed date so results are reproducible
DOSE_RE = re.compile(r"\b\d+(\.\d+)?\s?(mg|ml|milligrams?|tablets?|units?|cc)\b", re.I)


def household(setup: str | None = None) -> Owner:
    owner = Owner(name="Sam", available_minutes=120, day_start="07:00")
    biscuit = Pet("Biscuit", "dog", "beagle", 9)
    biscuit.add_task(Task("Breakfast", 10, "high", "feeding", "08:00", due_date=DAY))
    if setup == "vet_call_1820":
        biscuit.add_task(Task("Vet call", 30, "high", "general", "18:20", due_date=DAY))
    owner.add_pet(biscuit)
    owner.add_pet(Pet("Mochi", "cat", "", 4))
    return owner


def check(result: AgentResult, expect: dict) -> list[str]:
    """Return a list of failed expectations (empty list = pass)."""
    fails = []
    if result.status != expect["status"]:
        fails.append(f"status {result.status} != {expect['status']}")
    n = len(result.tasks)
    if "min_tasks" in expect and n < expect["min_tasks"]:
        fails.append(f"{n} tasks < {expect['min_tasks']}")
    if "max_tasks" in expect and n > expect["max_tasks"]:
        fails.append(f"{n} tasks > {expect['max_tasks']}")
    if "top_source" in expect and (not result.sources or result.sources[0]["id"] != expect["top_source"]):
        fails.append(f"top source {result.sources[0]['id'] if result.sources else None}")
    if expect.get("grounded"):
        ids = {s["id"] for s in result.sources} | {"user"}
        bad = [t["title"] for t in result.tasks if t["source"] not in ids]
        if bad:
            fails.append(f"ungrounded: {bad}")
    for category in expect.get("categories", []):
        if category not in {t["category"] for t in result.tasks}:
            fails.append(f"missing {category} task")
    if "no_duplicate" in expect and expect["no_duplicate"] in [t["title"] for t in result.tasks]:
        fails.append(f"duplicated {expect['no_duplicate']}")
    for time in expect.get("task_times", []):
        if time not in [t["time"] for t in result.tasks]:
            fails.append(f"no task at {time}")
    if expect.get("meds_high") and any(t["priority"] != "high" for t in result.tasks if t["category"] == "meds"):
        fails.append("meds not high priority")
    if "budget" in expect and sum(s.task.duration_minutes for s in result.plan) > expect["budget"]:
        fails.append("plan exceeds time budget")
    if "answer_contains" in expect and expect["answer_contains"].lower() not in result.answer.lower():
        fails.append(f"answer missing '{expect['answer_contains']}'")
    if expect.get("no_dose_amounts") and DOSE_RE.search(result.answer):
        fails.append("answer contains a dose amount")
    if expect.get("no_conflicts") and result.conflicts:
        fails.append(f"{len(result.conflicts)} conflict(s) left")
    if "repair_contains" in expect and not any(expect["repair_contains"] in r for r in result.repairs):
        fails.append(f"no repair '{expect['repair_contains']}'")
    return fails


def run(backend: str | None, style: str, use_retrieval: bool, cases: list[dict], kb: KnowledgeBase) -> list[dict]:
    llm = make_client(backend)
    rows = []
    for case in cases:
        agent = CareAgent(household(case.get("setup")), llm=llm, knowledge_base=kb, style=style, day=DAY,
                          use_retrieval=use_retrieval)
        result = agent.run(case["request"])
        rows.append({"case": case, "result": result, "fails": check(result, case["expect"])})
    return rows


def metrics(rows: list[dict]) -> dict:
    ok = [r["result"] for r in rows if r["result"].status == "ok"]
    answers = [r.answer for r in ok]
    all_tasks = [t for r in ok for t in r.tasks]
    guard_cases = [r for r in rows if r["case"]["expect"]["status"] != "ok"]
    return {
        "passed": sum(not r["fails"] for r in rows),
        "total": len(rows),
        "avg_confidence": round(sum(r.confidence for r in ok) / len(ok), 2) if ok else 0.0,
        "grounded_task_rate": round(sum(t["source"] != "" for t in all_tasks) / len(all_tasks), 2) if all_tasks else 0.0,
        "guardrail_catches": f"{sum(r['result'].status == r['case']['expect']['status'] for r in guard_cases)}/{len(guard_cases)}",
        "avg_answer_words": round(sum(len(a.split()) for a in answers) / len(answers), 1) if answers else 0.0,
        "answers_with_citations": round(sum(bool(re.search(r"\[\d+\]", a)) for a in answers) / len(answers), 2) if answers else 0.0,
        "answers_with_bullets": round(sum("\n- " in a for a in answers) / len(answers), 2) if answers else 0.0,
        "avg_tasks_per_plan": round(len(all_tasks) / len(ok), 2) if ok else 0.0,
        "dose_amount_leaks": sum(bool(DOSE_RE.search(a)) for a in answers),
    }


def print_table(rows: list[dict], label: str) -> str:
    lines = [f"PawPal+ evaluation - {label}", "=" * 96,
             f"{'case':<24} {'status':<10} {'tasks':>5} {'conf':>5}  result"]
    lines.append("-" * 96)
    for row in rows:
        r = row["result"]
        verdict = "PASS" if not row["fails"] else "FAIL: " + "; ".join(row["fails"])
        lines.append(f"{row['case']['id']:<24} {r.status:<10} {len(r.tasks):>5} {r.confidence:>5.2f}  {verdict}")
    m = metrics(rows)
    lines.append("-" * 96)
    lines.append(f"Passed {m['passed']}/{m['total']} ({100 * m['passed'] / m['total']:.0f}%) | "
                 f"avg confidence (ok cases) {m['avg_confidence']:.2f} | grounded tasks {m['grounded_task_rate']:.0%} | "
                 f"guardrail catches {m['guardrail_catches']} | dose leaks {m['dose_amount_leaks']}")
    text = "\n".join(lines)
    print(text)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the PawPal+ AI Care Assistant")
    parser.add_argument("--backend", choices=["auto", "offline", "gemini"], default="offline")
    parser.add_argument("--style", choices=["pawpal", "baseline"], default="pawpal")
    parser.add_argument("--no-rag", action="store_true")
    parser.add_argument("--compare", action="store_true", help="Compare full system vs no-RAG vs baseline style")
    parser.add_argument("--save", action="store_true", help="Write results to eval/results.md and .json")
    args = parser.parse_args(argv)

    cases = json.loads((EVAL_DIR / "cases.json").read_text())
    kb = KnowledgeBase()

    if args.compare:
        configs = [("full system (RAG + PawPal style)", "pawpal", True),
                   ("no retrieval (RAG off)", "pawpal", False),
                   ("baseline style (no specialization)", "baseline", True)]
        table = ["| Metric | " + " | ".join(name for name, _, _ in configs) + " |",
                 "|---|" + "---|" * len(configs)]
        results = {name: metrics(run(args.backend, style, rag, cases, kb)) for name, style, rag in configs}
        for key in next(iter(results.values())):
            table.append(f"| {key} | " + " | ".join(str(results[name][key]) for name, _, _ in configs) + " |")
        text = f"Backend: {make_client(args.backend).name}, cases: {len(cases)}\n\n" + "\n".join(table)
        print(text)
        (EVAL_DIR / "comparison.md").write_text(f"# Configuration comparison\n\n{text}\n", encoding="utf-8")
        return 0

    rows = run(args.backend, args.style, not args.no_rag, cases, kb)
    label = f"backend={make_client(args.backend).name}, style={args.style}, rag={'off' if args.no_rag else 'on'}"
    text = print_table(rows, label)
    if args.save:
        (EVAL_DIR / "results.md").write_text(f"# Evaluation results\n\n```text\n{text}\n```\n", encoding="utf-8")
        payload = [{"id": r["case"]["id"], "request": r["case"]["request"], "status": r["result"].status,
                    "confidence": r["result"].confidence, "tasks": r["result"].tasks, "fails": r["fails"]}
                   for r in rows]
        (EVAL_DIR / "results.json").write_text(json.dumps({"summary": metrics(rows), "cases": payload}, indent=2))
    return 0 if all(not r["fails"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
