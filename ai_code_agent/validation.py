from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ValidationStep:
    label: str
    command: list[str]


def _run_step(step: ValidationStep) -> int:
    print(f"==> {step.label}")
    completed = subprocess.run(step.command, cwd=REPO_ROOT, check=False)
    if completed.returncode != 0:
        print(f"FAILED: {step.label} (exit={completed.returncode})")
        return completed.returncode
    print(f"PASSED: {step.label}")
    return 0


def main() -> int:
    steps = [
        ValidationStep("compileall", [sys.executable, "-m", "compileall", "ai_code_agent", "tests"]),
        ValidationStep("unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
        ValidationStep("nestjs smoke", [sys.executable, "artifact/run_nestjs_smoke.py"]),
        ValidationStep("nextjs visual review smoke", [sys.executable, "artifact/run_nextjs_visual_review_smoke.py"]),
        ValidationStep("retrieval evaluation", [sys.executable, "artifact/run_retrieval_eval.py"]),
    ]

    for step in steps:
        exit_code = _run_step(step)
        if exit_code != 0:
            return exit_code

    print("Validation suite passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())