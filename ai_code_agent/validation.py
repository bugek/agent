from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ValidationStep:
    label: str
    command: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repository validation steps.")
    parser.add_argument(
        "--mode",
        choices=["quick", "full"],
        default="full",
        help="Validation mode: quick runs compile and unit tests only; full adds framework smoke checks and retrieval evaluation.",
    )
    return parser.parse_args(argv)


def get_validation_steps(mode: str) -> list[ValidationStep]:
    quick_steps = [
        ValidationStep("compileall", [sys.executable, "-m", "compileall", "ai_code_agent", "tests"]),
        ValidationStep("unit tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]),
    ]
    if mode == "quick":
        return quick_steps
    if mode != "full":
        raise ValueError(f"Unsupported validation mode: {mode}")
    return quick_steps + [
        ValidationStep("nestjs smoke", [sys.executable, "artifact/run_nestjs_smoke.py"]),
        ValidationStep("nextjs visual review smoke", [sys.executable, "artifact/run_nextjs_visual_review_smoke.py"]),
        ValidationStep("retrieval evaluation", [sys.executable, "artifact/run_retrieval_eval.py"]),
    ]


def _run_step(step: ValidationStep) -> int:
    print(f"==> {step.label}")
    completed = subprocess.run(step.command, cwd=REPO_ROOT, check=False)
    if completed.returncode != 0:
        print(f"FAILED: {step.label} (exit={completed.returncode})")
        return completed.returncode
    print(f"PASSED: {step.label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    steps = get_validation_steps(args.mode)

    for step in steps:
        exit_code = _run_step(step)
        if exit_code != 0:
            return exit_code

    print(f"Validation suite passed ({args.mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())