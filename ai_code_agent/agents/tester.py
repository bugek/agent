import json
from pathlib import Path
from typing import Any

VISUAL_REVIEW_ROOT = Path(".ai-code-agent") / "visual-review"
VISUAL_REVIEW_MANIFEST_FILE = "manifest.json"
VISUAL_REVIEW_SCREENSHOTS_DIR = "screenshots"
VISUAL_REVIEW_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
RESPONSIVE_REQUIRED_CATEGORIES = {"mobile", "desktop"}

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.metrics import list_execution_metrics_artifacts
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.tools.linter import LinterTool
from ai_code_agent.tools.sandbox import SandboxRunner
from ai_code_agent.tools.workspace_profile import detect_workspace_profile

class TesterAgent(BaseAgent):
    """
    Agent responsible for running code in the Sandbox environment.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Executes unit tests or runs a reproducible script in the Sandbox.
        """
        workspace_profile = detect_workspace_profile(state["workspace_dir"])
        sandbox = SandboxRunner(
            container_image=self.config.docker_image,
            workspace_dir=state["workspace_dir"],
            mode=self.config.sandbox_mode,
        )
        sandbox_startup = sandbox.start_container()

        validation_plan = self._build_validation_plan(state, workspace_profile)
        command_results = self._run_validation_commands(sandbox, validation_plan["commands"])

        lint_output = []
        linter = LinterTool(state["workspace_dir"])
        for file_path in state.get("files_to_edit", [])[:10]:
            if not file_path.endswith((".py", ".js", ".jsx", ".ts", ".tsx")):
                continue
            result = linter.run_linter(file_path)
            if result:
                lint_output.append(f"[{file_path}] {result}")

        sandbox.cleanup()
        test_passed = all(result["exit_code"] == 0 for result in command_results)
        combined_output = [
            f"{result['label']}(exit={result['exit_code']}):\n{result['stdout']}{result['stderr']}"
            for result in command_results
        ]
        if lint_output:
            combined_output.append("lint:\n" + "\n".join(lint_output))

        return {
            "test_passed": test_passed,
            "test_results": "\n\n".join(combined_output).strip(),
            "testing_summary": self._build_testing_summary(command_results, lint_output, validation_plan, sandbox_startup),
            "visual_review": self._build_visual_review(state, workspace_profile, command_results),
        }

    def _run_validation_commands(
        self,
        sandbox: SandboxRunner,
        commands: list[tuple[str, str, int, dict[str, str] | None]],
    ) -> list[dict]:
        results: list[dict] = []

        for label, command, timeout, env in commands:
            result = sandbox.execute(command, timeout=timeout, env=env)
            result["label"] = label
            results.append(result)
            if result["exit_code"] != 0:
                break

        return results

    def _build_testing_summary(
        self,
        command_results: list[dict],
        lint_output: list[str],
        validation_plan: dict[str, Any] | None = None,
        sandbox_startup: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        commands: list[dict[str, object]] = []
        total_duration_ms = 0
        for result in command_results:
            duration_ms = result.get("duration_ms") if isinstance(result.get("duration_ms"), int) else 0
            total_duration_ms += max(0, duration_ms)
            commands.append(
                {
                    "label": result.get("label"),
                    "exit_code": result.get("exit_code"),
                    "duration_ms": max(0, duration_ms),
                    "mode": result.get("mode"),
                    "timed_out": bool(result.get("timed_out", False)),
                }
            )

        failed_commands = [
            command["label"]
            for command in commands
            if isinstance(command.get("label"), str) and isinstance(command.get("exit_code"), int) and command["exit_code"] != 0
        ]
        slowest_command = None
        if commands:
            slowest_command = max(commands, key=lambda command: command.get("duration_ms") or 0)

        return {
            "commands": commands,
            "command_count": len(commands),
            "failed_command_count": len(failed_commands),
            "failed_commands": failed_commands,
            "lint_issue_count": len(lint_output),
            "total_duration_ms": total_duration_ms,
            "slowest_command": slowest_command,
            "validation_strategy": validation_plan.get("strategy", "full") if isinstance(validation_plan, dict) else "full",
            "selected_command_labels": validation_plan.get("selected_labels", []) if isinstance(validation_plan, dict) else [],
            "skipped_command_labels": validation_plan.get("skipped_labels", []) if isinstance(validation_plan, dict) else [],
            "requested_retry_labels": validation_plan.get("requested_retry_labels", []) if isinstance(validation_plan, dict) else [],
            "retry_policy_reason": validation_plan.get("policy_reason") if isinstance(validation_plan, dict) else None,
            "retry_policy_history_source": validation_plan.get("history_source") if isinstance(validation_plan, dict) else None,
            "retry_policy_confidence": validation_plan.get("policy_confidence") if isinstance(validation_plan, dict) else None,
            "stop_retry_after_failure": bool(validation_plan.get("stop_retry_after_failure", False)) if isinstance(validation_plan, dict) else False,
            "retry_policy_stop_reason": validation_plan.get("stop_reason") if isinstance(validation_plan, dict) else None,
            "sandbox_requested_mode": sandbox_startup.get("requested_mode") if isinstance(sandbox_startup, dict) else None,
            "sandbox_mode": sandbox_startup.get("resolved_mode") if isinstance(sandbox_startup, dict) else (commands[0].get("mode") if commands else None),
            "sandbox_started": bool(sandbox_startup.get("started", False)) if isinstance(sandbox_startup, dict) else True,
            "sandbox_fallback_reason": sandbox_startup.get("fallback_reason") if isinstance(sandbox_startup, dict) else None,
        }

    def _build_validation_plan(self, state: AgentState, workspace_profile: dict) -> dict[str, Any]:
        full_commands = self._build_validation_commands(state, workspace_profile)
        selection = self._select_retry_commands(state, full_commands)
        selected_commands = selection["commands"]
        requested_retry_labels = selection["requested_retry_labels"]
        selected_labels = [label for label, _, _, _ in selected_commands]
        full_labels = [label for label, _, _, _ in full_commands]
        skipped_labels = [label for label in full_labels if label not in selected_labels]
        strategy = "targeted_retry" if selected_labels != full_labels else "full"
        return {
            "strategy": strategy,
            "commands": selected_commands,
            "selected_labels": selected_labels,
            "skipped_labels": skipped_labels,
            "requested_retry_labels": requested_retry_labels,
            "policy_reason": selection.get("policy_reason"),
            "history_source": selection.get("history_source"),
            "policy_confidence": selection.get("policy_confidence"),
            "stop_retry_after_failure": bool(selection.get("stop_retry_after_failure", False)),
            "stop_reason": selection.get("stop_reason"),
        }

    def _build_validation_commands(self, state: AgentState, workspace_profile: dict) -> list[tuple[str, str, int, dict[str, str] | None]]:
        commands: list[tuple[str, str, int, dict[str, str] | None]] = []

        if workspace_profile.get("has_python"):
            commands.append(("compileall", "python -m compileall ai_code_agent", 120, None))
            commands.append(("cli-help", "python -m ai_code_agent.main run --help", 120, None))

        if workspace_profile.get("has_package_json"):
            install_command = self._install_command(workspace_profile)
            if install_command:
                commands.append(("package-install", install_command, 900, None))

            commands.extend(self._build_javascript_validation_commands(state, workspace_profile))

        return commands

    def _select_retry_commands(
        self,
        state: AgentState,
        full_commands: list[tuple[str, str, int, dict[str, str] | None]],
    ) -> dict[str, Any]:
        if int(state.get("retry_count", 0) or 0) <= 0:
            return self._selection_result(full_commands, [], "initial_full_validation", None, None, False, None)

        review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
        remediation = review_summary.get("remediation") if isinstance(review_summary.get("remediation"), dict) else {}
        if not remediation.get("required"):
            return self._selection_result(full_commands, [], "remediation_not_required", None, None, False, None)

        requested_labels = list(
            dict.fromkeys(
                [
                    label
                    for label in remediation.get("failed_validation_labels", [])
                    if isinstance(label, str) and label
                ]
                + [
                    label
                    for label in (state.get("testing_summary") or {}).get("failed_commands", [])
                    if isinstance(label, str) and label
                ]
                + self._visual_retry_labels(state, full_commands)
            )
        )
        if not requested_labels:
            return self._selection_result(full_commands, [], "no_retry_signals", None, None, False, None)

        previous_strategy = None
        prior_testing_summary = state.get("testing_summary") if isinstance(state.get("testing_summary"), dict) else {}
        if isinstance(prior_testing_summary.get("validation_strategy"), str):
            previous_strategy = prior_testing_summary.get("validation_strategy")

        if previous_strategy == "targeted_retry" and int(state.get("retry_count", 0) or 0) > 1:
            return self._selection_result(
                full_commands,
                requested_labels,
                "fallback_to_full_after_targeted_retry",
                "previous_attempt",
                "strong",
                True,
                "failed_targeted_retry_then_full_fallback",
            )

        failure_category = self._retry_failure_category(state)
        history_profile = self._historical_retry_strategy_profile(
            state.get("workspace_dir", "."),
            state.get("run_id"),
            failure_category,
        )
        if history_profile.get("preferred_strategy") == "full":
            return self._selection_result(
                full_commands,
                requested_labels,
                "history_prefers_full",
                history_profile.get("source"),
                history_profile.get("confidence"),
                bool(history_profile.get("stop_after_failure", False)),
                history_profile.get("stop_reason"),
            )

        selected: list[tuple[str, str, int, dict[str, str] | None]] = []
        for command in full_commands:
            label = command[0]
            if label == "package-install":
                continue
            if label in requested_labels:
                selected.append(command)

        if not selected:
            return self._selection_result(
                full_commands,
                requested_labels,
                "requested_labels_not_available",
                history_profile.get("source"),
                history_profile.get("confidence"),
                bool(history_profile.get("stop_after_failure", False)),
                history_profile.get("stop_reason"),
            )

        needs_install = any(label != "compileall" and label != "cli-help" for label, _, _, _ in selected)
        package_install = next((command for command in full_commands if command[0] == "package-install"), None)
        if package_install is not None and needs_install:
            selected.insert(0, package_install)

        policy_reason = "default_targeted_retry"
        if history_profile.get("preferred_strategy") == "targeted_retry":
            policy_reason = "history_prefers_targeted_retry"
        return self._selection_result(
            selected,
            requested_labels,
            policy_reason,
            history_profile.get("source"),
            history_profile.get("confidence"),
            bool(history_profile.get("stop_after_failure", False)),
            history_profile.get("stop_reason"),
        )

    def _selection_result(
        self,
        commands: list[tuple[str, str, int, dict[str, str] | None]],
        requested_retry_labels: list[str],
        policy_reason: str | None,
        history_source: str | None,
        policy_confidence: str | None,
        stop_retry_after_failure: bool,
        stop_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "commands": commands,
            "requested_retry_labels": requested_retry_labels,
            "policy_reason": policy_reason,
            "history_source": history_source,
            "policy_confidence": policy_confidence,
            "stop_retry_after_failure": stop_retry_after_failure,
            "stop_reason": stop_reason,
        }

    def _retry_failure_category(self, state: AgentState) -> str | None:
        execution_metrics = state.get("execution_metrics") if isinstance(state.get("execution_metrics"), dict) else {}
        failures = execution_metrics.get("failures") if isinstance(execution_metrics.get("failures"), dict) else {}
        primary_category = failures.get("primary_category")
        return primary_category if isinstance(primary_category, str) and primary_category else None

    def _historical_retry_strategy_profile(
        self,
        workspace_dir: str,
        current_run_id: str | None,
        failure_category: str | None,
    ) -> dict[str, Any]:
        category_stats = self._collect_strategy_stats(workspace_dir, current_run_id, failure_category)
        if self._has_comparable_history(category_stats):
            return self._build_history_profile(category_stats, "failure_category")

        overall_stats = self._collect_strategy_stats(workspace_dir, current_run_id, None)
        if self._has_comparable_history(overall_stats):
            return self._build_history_profile(overall_stats, "overall")

        return {
            "source": None,
            "preferred_strategy": None,
            "confidence": None,
            "stop_after_failure": False,
            "stop_reason": None,
            "stats": overall_stats,
        }

    def _build_history_profile(self, stats: dict[str, dict[str, float | int]], source: str) -> dict[str, Any]:
        preferred_strategy = self._preferred_strategy(stats)
        confidence = self._strategy_confidence(stats, preferred_strategy)
        stop_after_failure, stop_reason = self._history_stop_signal(stats, preferred_strategy)
        return {
            "source": source,
            "preferred_strategy": preferred_strategy,
            "confidence": confidence,
            "stop_after_failure": stop_after_failure,
            "stop_reason": stop_reason,
            "stats": stats,
        }

    def _collect_strategy_stats(
        self,
        workspace_dir: str,
        current_run_id: str | None,
        failure_category: str | None,
    ) -> dict[str, dict[str, float | int]]:
        metrics_entries = list_execution_metrics_artifacts(workspace_dir, limit=max(1, self.config.retry_history_window))
        stats: dict[str, dict[str, float | int]] = {
            "full": {"run_count": 0, "approved_count": 0, "total_testing_duration_ms": 0},
            "targeted_retry": {"run_count": 0, "approved_count": 0, "total_testing_duration_ms": 0},
        }

        for metrics, _ in metrics_entries:
            if not isinstance(metrics, dict):
                continue
            if current_run_id and metrics.get("run_id") == current_run_id:
                continue
            workflow = metrics.get("workflow") if isinstance(metrics.get("workflow"), dict) else {}
            testing = metrics.get("testing") if isinstance(metrics.get("testing"), dict) else {}
            failures = metrics.get("failures") if isinstance(metrics.get("failures"), dict) else {}
            if _as_int(workflow.get("attempt_count")) <= 1:
                continue
            if workflow.get("status") == "aborted":
                continue
            if failure_category and failures.get("primary_category") != failure_category:
                continue
            strategy = testing.get("validation_strategy") if testing.get("validation_strategy") == "targeted_retry" else "full"
            bucket = stats[strategy]
            bucket["run_count"] += 1
            bucket["total_testing_duration_ms"] += _as_int(testing.get("total_duration_ms"))
            if workflow.get("status") == "approved":
                bucket["approved_count"] += 1

        return stats

    def _has_comparable_history(self, stats: dict[str, dict[str, float | int]]) -> bool:
        return any(_as_int(bucket.get("run_count")) >= self.config.retry_policy_min_samples for bucket in stats.values())

    def _preferred_strategy(self, stats: dict[str, dict[str, float | int]]) -> str | None:
        full_summary = self._strategy_summary(stats.get("full", {}))
        targeted_summary = self._strategy_summary(stats.get("targeted_retry", {}))

        if targeted_summary["run_count"] < self.config.retry_policy_min_samples and full_summary["run_count"] < self.config.retry_policy_min_samples:
            return None
        if targeted_summary["run_count"] < self.config.retry_policy_min_samples:
            return "full"
        if full_summary["run_count"] < self.config.retry_policy_min_samples:
            return "targeted_retry"
        if full_summary["success_rate"] > targeted_summary["success_rate"]:
            return "full"
        if targeted_summary["success_rate"] > full_summary["success_rate"]:
            return "targeted_retry"
        if full_summary["average_testing_duration_ms"] < targeted_summary["average_testing_duration_ms"]:
            return "full"
        return "targeted_retry"

    def _strategy_confidence(self, stats: dict[str, dict[str, float | int]], preferred_strategy: str | None) -> str | None:
        if preferred_strategy is None:
            return None
        full_summary = self._strategy_summary(stats.get("full", {}))
        targeted_summary = self._strategy_summary(stats.get("targeted_retry", {}))
        if full_summary["run_count"] < self.config.retry_policy_min_samples or targeted_summary["run_count"] < self.config.retry_policy_min_samples:
            return "limited"
        success_gap = abs(float(full_summary["success_rate"]) - float(targeted_summary["success_rate"]))
        if success_gap >= self.config.retry_policy_min_confidence_gap:
            return "strong"
        return "weak"

    def _history_stop_signal(
        self,
        stats: dict[str, dict[str, float | int]],
        preferred_strategy: str | None,
    ) -> tuple[bool, str | None]:
        if preferred_strategy is None:
            return False, None
        full_summary = self._strategy_summary(stats.get("full", {}))
        targeted_summary = self._strategy_summary(stats.get("targeted_retry", {}))
        if full_summary["run_count"] < self.config.retry_policy_min_samples or targeted_summary["run_count"] < self.config.retry_policy_min_samples:
            return False, None
        if float(full_summary["success_rate"]) <= self.config.retry_policy_stop_success_rate and float(targeted_summary["success_rate"]) <= self.config.retry_policy_stop_success_rate:
            return True, "history_low_recovery_probability"
        return False, None

    def _strategy_summary(self, bucket: dict[str, float | int]) -> dict[str, float | int]:
        run_count = _as_int(bucket.get("run_count"))
        approved_count = _as_int(bucket.get("approved_count"))
        total_testing_duration_ms = _as_int(bucket.get("total_testing_duration_ms"))
        return {
            "run_count": run_count,
            "success_rate": round(approved_count / run_count, 2) if run_count else 0.0,
            "average_testing_duration_ms": int(total_testing_duration_ms / run_count) if run_count else 0,
        }

    def _visual_retry_labels(
        self,
        state: AgentState,
        full_commands: list[tuple[str, str, int, dict[str, str] | None]],
    ) -> list[str]:
        review_summary = state.get("review_summary") if isinstance(state.get("review_summary"), dict) else {}
        visual_review = review_summary.get("visual_review") if isinstance(review_summary.get("visual_review"), dict) else {}
        screenshot_status = visual_review.get("screenshot_status")
        missing_states = visual_review.get("missing_states") or []
        missing_responsive = visual_review.get("missing_responsive_categories") or []
        if screenshot_status not in {"failed", "missing_artifacts"} and not missing_states and not missing_responsive:
            return []
        return [
            label
            for label, _, _, _ in full_commands
            if label in {"script:visual-review", "script:screenshot", "script:test:visual"}
        ]

    def _install_command(self, workspace_profile: dict) -> str | None:
        if not workspace_profile.get("needs_install"):
            return None

        package_manager = workspace_profile.get("package_manager") or "npm"
        if package_manager == "pnpm":
            return "pnpm install --frozen-lockfile"
        if package_manager == "yarn":
            return "yarn install --frozen-lockfile"
        if workspace_profile.get("package_manager") == "npm":
            return "npm ci" if "package-lock.json" in workspace_profile.get("lockfiles", []) else "npm install"
        return "npm install"

    def _run_script_command(self, workspace_profile: dict, script_name: str) -> str:
        package_manager = workspace_profile.get("package_manager") or "npm"
        if package_manager == "pnpm":
            return f"pnpm run {script_name}"
        if package_manager == "yarn":
            return f"yarn {script_name}"
        return f"npm run {script_name}"

    def _build_javascript_validation_commands(self, state: AgentState, workspace_profile: dict) -> list[tuple[str, str, int, dict[str, str] | None]]:
        commands: list[tuple[str, str, int, dict[str, str] | None]] = []
        scripts = set(workspace_profile.get("scripts", []))

        if "nextjs" in workspace_profile.get("frameworks", []):
            commands.extend(self._build_nextjs_commands(state, workspace_profile, scripts))
        elif "nestjs" in workspace_profile.get("frameworks", []):
            commands.extend(self._build_nestjs_commands(workspace_profile, scripts))
        else:
            for script_name in ["lint", "typecheck", "build", "test"]:
                if script_name in scripts:
                    commands.append((f"script:{script_name}", self._run_script_command(workspace_profile, script_name), 900, None))

        return commands

    def _build_nextjs_commands(self, state: AgentState, workspace_profile: dict, scripts: set[str]) -> list[tuple[str, str, int, dict[str, str] | None]]:
        commands: list[tuple[str, str, int, dict[str, str] | None]] = []
        nextjs_profile = workspace_profile.get("nextjs") or {}
        visual_review_env = self._visual_review_environment(state["workspace_dir"])

        if "lint" in scripts:
            commands.append(("script:lint", self._run_script_command(workspace_profile, "lint"), 900, None))

        if "typecheck" in scripts:
            commands.append(("script:typecheck", self._run_script_command(workspace_profile, "typecheck"), 900, None))
        elif self._has_typescript_config(workspace_profile):
            commands.append(("typescript:noEmit", self._exec_command(workspace_profile, "tsc --noEmit"), 900, None))

        if "build" in scripts:
            commands.append(("script:build", self._run_script_command(workspace_profile, "build"), 900, None))
        elif self._has_local_bin(workspace_profile, "next"):
            commands.append(("next:build", self._exec_command(workspace_profile, "next build"), 900, None))

        if "test" in scripts:
            commands.append(("script:test", self._run_script_command(workspace_profile, "test"), 900, None))

        for script_name in ["visual-review", "screenshot", "test:visual"]:
            if script_name in scripts:
                commands.append((f"script:{script_name}", self._run_script_command(workspace_profile, script_name), 1200, visual_review_env))
                break

        if nextjs_profile.get("router_type") == "app":
            commands.append(("next:router-detected", "python -c \"print('nextjs app router detected')\"", 30, None))
        elif nextjs_profile.get("router_type") == "pages":
            commands.append(("next:router-detected", "python -c \"print('nextjs pages router detected')\"", 30, None))

        return commands

    def _build_nestjs_commands(self, workspace_profile: dict, scripts: set[str]) -> list[tuple[str, str, int, dict[str, str] | None]]:
        commands: list[tuple[str, str, int, dict[str, str] | None]] = []
        nestjs_profile = workspace_profile.get("nestjs") or {}
        has_typescript = bool(nestjs_profile.get("has_typescript"))
        has_nest_cli = bool(nestjs_profile.get("has_nest_cli"))

        if "lint" in scripts:
            commands.append(("script:lint", self._run_script_command(workspace_profile, "lint"), 900, None))

        if "typecheck" in scripts:
            commands.append(("script:typecheck", self._run_script_command(workspace_profile, "typecheck"), 900, None))
        elif has_typescript and self._has_typescript_config(workspace_profile):
            tsconfig_build = nestjs_profile.get("tsconfig_build") or "tsconfig.build.json"
            if tsconfig_build in workspace_profile.get("priority_files", []):
                commands.append(("typescript:build-check", self._exec_command(workspace_profile, f"tsc -p {tsconfig_build} --noEmit"), 900, None))
            else:
                commands.append(("typescript:noEmit", self._exec_command(workspace_profile, "tsc --noEmit"), 900, None))

        if "build" in scripts:
            commands.append(("script:build", self._run_script_command(workspace_profile, "build"), 900, None))
        elif has_nest_cli and self._has_local_bin(workspace_profile, "nest"):
            commands.append(("nest:build", self._exec_command(workspace_profile, "nest build"), 900, None))

        if "test" in scripts:
            commands.append(("script:test", self._run_script_command(workspace_profile, "test"), 900, None))

        main_file = nestjs_profile.get("main_file") or "src/main.ts"
        commands.append(("nest:structure-detected", f"python -c \"print('nestjs workspace detected: {main_file}')\"", 30, None))

        return commands

    def _exec_command(self, workspace_profile: dict, command: str) -> str:
        package_manager = workspace_profile.get("package_manager") or "npm"
        if package_manager == "pnpm":
            return f"pnpm exec {command}"
        if package_manager == "yarn":
            return f"yarn {command}"
        return f"npx {command}"

    def _has_local_bin(self, workspace_profile: dict, binary_name: str) -> bool:
        return workspace_profile.get("needs_install") is False

    def _has_typescript_config(self, workspace_profile: dict) -> bool:
        return bool(workspace_profile.get("tsconfig_exists"))

    def _build_visual_review(self, state: AgentState, workspace_profile: dict, command_results: list[dict]) -> dict | None:
        planning_context = state.get("planning_context") or {}
        design_brief = planning_context.get("design_brief")
        nextjs_profile = workspace_profile.get("nextjs")
        if not nextjs_profile and not design_brief:
            return None

        changed_files = sorted(
            {
                patch.get("file")
                for patch in state.get("patches", [])
                if isinstance(patch, dict) and patch.get("file")
            }
        )
        if not changed_files:
            changed_files = [file_path for file_path in state.get("files_to_edit", []) if isinstance(file_path, str)]

        route_files = [file_path for file_path in changed_files if self._is_next_route_file(file_path)]
        component_files = [file_path for file_path in changed_files if self._is_next_component_file(file_path)]
        loading_files = [file_path for file_path in changed_files if file_path.endswith("loading.tsx") or file_path.endswith("loading.ts")]
        error_files = [file_path for file_path in changed_files if file_path.endswith("error.tsx") or file_path.endswith("error.ts")]

        state_files = sorted({*route_files, *component_files, *loading_files, *error_files})

        state_coverage = {
            "loading_file": bool(loading_files),
            "error_file": bool(error_files),
            "loading_state": self._any_file_contains_any(
                state["workspace_dir"],
                state_files,
                ['state === "loading"', "state === 'loading'", 'state = "loading"', "state = 'loading'"],
            ),
            "empty_state": self._any_file_contains_any(
                state["workspace_dir"],
                state_files,
                ['state === "empty"', "state === 'empty'", 'state = "empty"', "state = 'empty'"],
            ),
            "error_state": self._any_file_contains_any(
                state["workspace_dir"],
                state_files,
                ['state === "error"', "state === 'error'", 'state = "error"', "state = 'error'"],
            ),
            "success_state": self._any_file_contains_any(
                state["workspace_dir"],
                state_files,
                [
                    'state = "success"',
                    'state === "success"',
                    "state = 'success'",
                    "state === 'success'",
                    'state = "ready"',
                    'state === "ready"',
                    "state = 'ready'",
                    "state === 'ready'",
                ],
            ),
        }

        screenshot_signal = self._visual_review_command_signal(command_results)
        artifact_metadata = self._collect_visual_review_artifacts(state["workspace_dir"], screenshot_signal)
        screenshot_status = "not_configured"
        if screenshot_signal is not None:
            if screenshot_signal.get("exit_code") != 0:
                screenshot_status = "failed"
            elif artifact_metadata["artifact_count"] > 0:
                screenshot_status = "passed"
            else:
                screenshot_status = "missing_artifacts"

        return {
            "enabled": True,
            "design_brief_present": isinstance(design_brief, dict),
            "design_brief": design_brief if isinstance(design_brief, dict) else None,
            "route_files": route_files,
            "component_files": component_files,
            "loading_files": loading_files,
            "error_files": error_files,
            "state_coverage": state_coverage,
            "screenshot_status": screenshot_status,
            "screenshot_signal": screenshot_signal,
            "artifact_manifest": artifact_metadata["artifact_manifest"],
            "artifact_dir": artifact_metadata["artifact_dir"],
            "artifact_count": artifact_metadata["artifact_count"],
            "artifacts": artifact_metadata["artifacts"],
            "artifact_summary": artifact_metadata["artifact_summary"],
            "responsive_review": self._build_responsive_review(artifact_metadata["artifacts"], screenshot_status),
        }

    def _visual_review_command_signal(self, command_results: list[dict]) -> dict | None:
        for result in command_results:
            label = result.get("label")
            if isinstance(label, str) and label.startswith("script:") and any(
                token in label for token in ["visual-review", "screenshot", "test:visual"]
            ):
                return {
                    "label": label,
                    "exit_code": result.get("exit_code"),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                }
        return None

    def _visual_review_environment(self, workspace_dir: str) -> dict[str, str]:
        root_dir = Path(workspace_dir) / VISUAL_REVIEW_ROOT
        screenshots_dir = root_dir / VISUAL_REVIEW_SCREENSHOTS_DIR
        manifest_path = root_dir / VISUAL_REVIEW_MANIFEST_FILE
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        return {
            "AI_CODE_AGENT_VISUAL_REVIEW_DIR": str(root_dir),
            "AI_CODE_AGENT_VISUAL_REVIEW_MANIFEST": str(manifest_path),
            "AI_CODE_AGENT_PLAYWRIGHT_SCREENSHOT_DIR": str(screenshots_dir),
        }

    def _collect_visual_review_artifacts(self, workspace_dir: str, screenshot_signal: dict | None) -> dict[str, object]:
        root_dir = Path(workspace_dir) / VISUAL_REVIEW_ROOT
        manifest_path = root_dir / VISUAL_REVIEW_MANIFEST_FILE
        manifest = self._load_visual_review_manifest(manifest_path)
        artifacts = self._resolve_visual_review_artifacts(workspace_dir, root_dir, manifest)

        if not artifacts:
            artifacts = self._discover_visual_review_artifacts(workspace_dir, root_dir)

        return {
            "artifact_manifest": self._relative_workspace_path(workspace_dir, manifest_path) if manifest_path.exists() else None,
            "artifact_dir": self._relative_workspace_path(workspace_dir, root_dir) if root_dir.exists() else None,
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
            "artifact_summary": {
                "tool": manifest.get("tool") if isinstance(manifest, dict) else None,
                "generated_at": manifest.get("generated_at") if isinstance(manifest, dict) else None,
                "manifest_detected": manifest is not None,
                "command_label": screenshot_signal.get("label") if isinstance(screenshot_signal, dict) else None,
            },
        }

    def _load_visual_review_manifest(self, manifest_path: Path) -> dict | None:
        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _resolve_visual_review_artifacts(self, workspace_dir: str, root_dir: Path, manifest: dict | None) -> list[dict[str, object]]:
        if not isinstance(manifest, dict):
            return []
        raw_artifacts = manifest.get("artifacts")
        if not isinstance(raw_artifacts, list):
            return []

        artifacts: list[dict[str, object]] = []
        for entry in raw_artifacts:
            if not isinstance(entry, dict):
                continue
            resolved = self._artifact_metadata_from_manifest_entry(workspace_dir, root_dir, entry)
            if resolved:
                artifacts.append(resolved)
        return artifacts

    def _artifact_metadata_from_manifest_entry(self, workspace_dir: str, root_dir: Path, entry: dict[str, object]) -> dict[str, object] | None:
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None

        candidate_path = Path(raw_path)
        if not candidate_path.is_absolute():
            candidate_path = root_dir / candidate_path
        if not candidate_path.exists() or not candidate_path.is_file():
            return None

        metadata = self._artifact_metadata_from_file(workspace_dir, candidate_path)
        if metadata is None:
            return None
        for key in ["kind", "route", "title", "status", "viewport", "device", "locale"]:
            value = entry.get(key)
            if value is not None:
                metadata[key] = value
        return metadata

    def _discover_visual_review_artifacts(self, workspace_dir: str, root_dir: Path) -> list[dict[str, object]]:
        if not root_dir.exists():
            return []

        artifacts: list[dict[str, object]] = []
        for file_path in sorted(root_dir.rglob("*")):
            if not file_path.is_file() or file_path.suffix.lower() not in VISUAL_REVIEW_SUPPORTED_EXTENSIONS:
                continue
            metadata = self._artifact_metadata_from_file(workspace_dir, file_path)
            if metadata is not None:
                artifacts.append(metadata)
        return artifacts

    def _artifact_metadata_from_file(self, workspace_dir: str, file_path: Path) -> dict[str, object] | None:
        try:
            stat_result = file_path.stat()
        except OSError:
            return None
        return {
            "path": self._relative_workspace_path(workspace_dir, file_path),
            "kind": "screenshot",
            "bytes": stat_result.st_size,
            "extension": file_path.suffix.lower(),
        }

    def _build_responsive_review(self, artifacts: list[dict[str, object]], screenshot_status: str) -> dict[str, object]:
        categories_present: set[str] = set()
        missing_viewport_metadata: list[str] = []

        for artifact in artifacts:
            category = self._viewport_category(artifact.get("viewport"))
            if category is not None:
                categories_present.add(category)
            elif artifact.get("kind") == "screenshot":
                path = artifact.get("path")
                if isinstance(path, str):
                    missing_viewport_metadata.append(path)

        missing_categories = sorted(RESPONSIVE_REQUIRED_CATEGORIES.difference(categories_present))
        return {
            "required_categories": sorted(RESPONSIVE_REQUIRED_CATEGORIES),
            "categories_present": sorted(categories_present),
            "missing_categories": missing_categories,
            "missing_viewport_metadata": missing_viewport_metadata,
            "passed": screenshot_status == "passed" and not missing_categories and not missing_viewport_metadata,
        }

    def _viewport_category(self, viewport: object) -> str | None:
        if not isinstance(viewport, dict):
            return None
        width = viewport.get("width")
        if not isinstance(width, int):
            return None
        if width < 768:
            return "mobile"
        if width >= 1024:
            return "desktop"
        return "tablet"

    def _relative_workspace_path(self, workspace_dir: str, target_path: Path) -> str:
        try:
            return target_path.relative_to(Path(workspace_dir)).as_posix()
        except ValueError:
            return target_path.as_posix()

    def _any_file_contains(self, workspace_dir: str, file_paths: list[str], text: str) -> bool:
        for file_path in file_paths:
            absolute_path = Path(workspace_dir) / file_path
            try:
                if text in absolute_path.read_text(encoding="utf-8"):
                    return True
            except OSError:
                continue
        return False

    def _any_file_contains_any(self, workspace_dir: str, file_paths: list[str], texts: list[str]) -> bool:
        for text in texts:
            if self._any_file_contains(workspace_dir, file_paths, text):
                return True
        return False

    def _is_next_route_file(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/")
        file_name = Path(normalized).name
        return file_name in {"page.tsx", "page.ts", "page.jsx", "page.js", "index.tsx", "index.ts", "index.jsx", "index.js"}

    def _is_next_component_file(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/")
        return "/components/" in f"/{normalized}" or normalized.startswith("components/")


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
