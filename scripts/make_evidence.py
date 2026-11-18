"""Regenerate every evidence file from real command output.

    python scripts/make_evidence.py            # offline backend (default, reproducible)
    PAWPAL_LLM=gemini python scripts/make_evidence.py   # same commands against Gemini

Writes docs/sample_runs.md, logs/traces/*, eval/results.*, eval/comparison.md.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COMMANDS = [
    ("Full scripted demo (5 requests, traces saved)", ["assistant.py", "--demo", "--save-trace"]),
    ("Owner task shortened to fit the time budget, then approved",
     ["assistant.py", "I only have 30 minutes today, plan Biscuit's walks", "--approve"]),
    ("Dosing question + reminder (AI-invented dose removed by the verifier)",
     ["assistant.py", "What dose of Benadryl should Mochi get? Also remind me about her allergy pill at 9am"]),
    ("RAG before/after: same request with retrieval turned off",
     ["assistant.py", "Plan a daily routine for Biscuit", "--no-rag"]),
    ("Specialization before/after: baseline (unspecialized) answer style",
     ["assistant.py", "Plan a daily routine for Biscuit", "--style", "baseline"]),
    ("Evaluation harness", ["-m", "eval.run_eval", "--save"]),
    ("Configuration comparison (full vs no-RAG vs baseline style)", ["-m", "eval.run_eval", "--compare"]),
    ("Test suite", ["-m", "pytest", "-q"]),
]


def shell_display(args: list[str]) -> str:
    return "python " + " ".join(f'"{a}"' if " " in a else a for a in args)


def main() -> None:
    shutil.rmtree(ROOT / "logs" / "traces", ignore_errors=True)
    lines = ["# Reproducible execution evidence", "",
             "Every block below is real output captured by `python scripts/make_evidence.py`.",
             "Re-run any command from the repo root to reproduce it.", ""]
    for title, args in COMMANDS:
        result = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
        output = (result.stdout + result.stderr).rstrip()
        lines += [f"## {title}", "", "```bash", f"$ {shell_display(args)}", "```", "", "```text", output, "```", ""]
    (ROOT / "docs" / "sample_runs.md").write_text("\n".join(lines), encoding="utf-8")

    # Extra traces that show the agent repairing its own plan.
    from eval.run_eval import DAY, household
    from pawpal_ai.agent import CareAgent, save_trace
    from pawpal_ai.llm import make_client

    for name, setup, request in [
        ("repair-meds-guardrail", None,
         "What dose of Benadryl should Mochi get? Also remind me about her allergy pill at 9am"),
        ("repair-conflict", "vet_call_1820", "Plan a daily routine for Biscuit"),
        ("repair-budget", None, "I only have 30 minutes today, plan Biscuit's walks"),
    ]:
        save_trace(CareAgent(household(setup), llm=make_client(), day=DAY).run(request), name=name)
    print("Evidence regenerated: docs/sample_runs.md, logs/traces/, eval/results.*, eval/comparison.md")


if __name__ == "__main__":
    main()
