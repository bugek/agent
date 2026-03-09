from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_code_agent.config import AgentConfig
from ai_code_agent.tools.sandbox import SandboxRunner


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
    parser.add_argument(
        "--require-docker-sandbox",
        action="store_true",
        help="Fail validation early unless the Docker sandbox backend is ready with the configured image.",
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
        ValidationStep("compose smoke", [sys.executable, "artifact/run_compose_smoke.py"]),
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


def sandbox_preflight(config: AgentConfig) -> dict[str, object]:
    return SandboxRunner(
        config.docker_image,
        workspace_dir=str(REPO_ROOT),
        mode=config.sandbox_mode,
        compose_file=config.sandbox_compose_file,
        compose_service=config.sandbox_compose_service,
        compose_project_name=config.sandbox_compose_project_name,
        compose_ready_services=config.sandbox_compose_ready_services,
        compose_readiness_timeout_seconds=config.sandbox_compose_readiness_timeout_seconds,
    ).probe()


def _print_sandbox_preflight(report: dict[str, object]) -> None:
    print("==> sandbox preflight")
    print(f"requested_mode={report.get('requested_mode')}")
    print(f"resolved_mode={report.get('resolved_mode')}")
    print(f"image={report.get('image')}")
    print(f"degraded={report.get('degraded')}")
    if report.get("fallback_reason"):
        print(f"fallback_reason={report.get('fallback_reason')}")
    if report.get("recommendation"):
        print(f"recommendation={report.get('recommendation')}")


def _sandbox_preflight_exit_code(report: dict[str, object], *, require_docker_sandbox: bool) -> int:
    if not require_docker_sandbox:
        return 0
    if report.get("docker_sandbox_ready"):
        return 0
    print("FAILED: sandbox preflight (docker sandbox required but not ready)")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = AgentConfig()
    preflight = sandbox_preflight(config)
    _print_sandbox_preflight(preflight)
    preflight_exit_code = _sandbox_preflight_exit_code(preflight, require_docker_sandbox=args.require_docker_sandbox)
    if preflight_exit_code != 0:
        return preflight_exit_code
    steps = get_validation_steps(args.mode)

    for step in steps:
        exit_code = _run_step(step)
        if exit_code != 0:
            return exit_code

    print(f"Validation suite passed ({args.mode}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())