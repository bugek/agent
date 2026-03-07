import json

from ai_code_agent.agents.base import BaseAgent
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
        sandbox.start_container()

        command_results = self._run_validation_commands(sandbox, workspace_profile)

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
        }

    def _run_validation_commands(self, sandbox: SandboxRunner, workspace_profile: dict) -> list[dict]:
        results: list[dict] = []

        for label, command, timeout in self._build_validation_commands(workspace_profile):
            result = sandbox.execute(command, timeout=timeout)
            result["label"] = label
            results.append(result)
            if result["exit_code"] != 0:
                break

        return results

    def _build_validation_commands(self, workspace_profile: dict) -> list[tuple[str, str, int]]:
        commands: list[tuple[str, str, int]] = []

        if workspace_profile.get("has_python"):
            commands.append(("compileall", "python -m compileall ai_code_agent", 120))
            commands.append(("cli-help", "python -m ai_code_agent.main run --help", 120))

        if workspace_profile.get("has_package_json"):
            install_command = self._install_command(workspace_profile)
            if install_command:
                commands.append(("package-install", install_command, 900))

            commands.extend(self._build_javascript_validation_commands(workspace_profile))

        return commands

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

    def _build_javascript_validation_commands(self, workspace_profile: dict) -> list[tuple[str, str, int]]:
        commands: list[tuple[str, str, int]] = []
        scripts = set(workspace_profile.get("scripts", []))

        if "nextjs" in workspace_profile.get("frameworks", []):
            commands.extend(self._build_nextjs_commands(workspace_profile, scripts))
        else:
            for script_name in ["lint", "typecheck", "build", "test"]:
                if script_name in scripts:
                    commands.append((f"script:{script_name}", self._run_script_command(workspace_profile, script_name), 900))

        return commands

    def _build_nextjs_commands(self, workspace_profile: dict, scripts: set[str]) -> list[tuple[str, str, int]]:
        commands: list[tuple[str, str, int]] = []
        nextjs_profile = workspace_profile.get("nextjs") or {}

        if "lint" in scripts:
            commands.append(("script:lint", self._run_script_command(workspace_profile, "lint"), 900))
        elif self._has_local_bin(workspace_profile, "next"):
            commands.append(("next:lint", self._exec_command(workspace_profile, "next lint"), 900))

        if "typecheck" in scripts:
            commands.append(("script:typecheck", self._run_script_command(workspace_profile, "typecheck"), 900))
        elif self._has_typescript_config(workspace_profile):
            commands.append(("typescript:noEmit", self._exec_command(workspace_profile, "tsc --noEmit"), 900))

        if "build" in scripts:
            commands.append(("script:build", self._run_script_command(workspace_profile, "build"), 900))
        elif self._has_local_bin(workspace_profile, "next"):
            commands.append(("next:build", self._exec_command(workspace_profile, "next build"), 900))

        if "test" in scripts:
            commands.append(("script:test", self._run_script_command(workspace_profile, "test"), 900))

        if nextjs_profile.get("router_type") == "app":
            commands.append(("next:router-detected", "python -c \"print('nextjs app router detected')\"", 30))
        elif nextjs_profile.get("router_type") == "pages":
            commands.append(("next:router-detected", "python -c \"print('nextjs pages router detected')\"", 30))

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
