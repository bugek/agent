import json
from pathlib import Path

from ai_code_agent.agents.base import BaseAgent
from ai_code_agent.orchestrator import AgentState
from ai_code_agent.tools.linter import LinterTool
from ai_code_agent.tools.sandbox import SandboxRunner

class TesterAgent(BaseAgent):
    """
    Agent responsible for running code in the Sandbox environment.
    """
    
    def run(self, state: AgentState) -> dict:
        """
        Executes unit tests or runs a reproducible script in the Sandbox.
        """
        workspace_profile = self._detect_workspace_profile(state["workspace_dir"])
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

            for script_name in ["lint", "typecheck", "build", "test"]:
                if script_name in workspace_profile.get("scripts", []):
                    commands.append((f"script:{script_name}", self._run_script_command(workspace_profile, script_name), 900))

        return commands

    def _detect_workspace_profile(self, workspace_dir: str) -> dict:
        root = Path(workspace_dir)
        package_json_path = root / "package.json"
        profile = {
            "has_python": (root / "pyproject.toml").exists(),
            "has_package_json": package_json_path.exists(),
            "scripts": [],
            "frameworks": [],
            "package_manager": None,
            "needs_install": not (root / "node_modules").exists(),
        }

        if package_json_path.exists():
            try:
                package_data = json.loads(package_json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package_data = {}
            scripts = package_data.get("scripts", {})
            deps = {
                **package_data.get("dependencies", {}),
                **package_data.get("devDependencies", {}),
            }
            profile["scripts"] = sorted(scripts.keys())

            if "next" in deps:
                profile["frameworks"].append("nextjs")
            if "@nestjs/core" in deps:
                profile["frameworks"].append("nestjs")

            if (root / "pnpm-lock.yaml").exists():
                profile["package_manager"] = "pnpm"
            elif (root / "yarn.lock").exists():
                profile["package_manager"] = "yarn"
            else:
                profile["package_manager"] = "npm"

        return profile

    def _install_command(self, workspace_profile: dict) -> str | None:
        if not workspace_profile.get("needs_install"):
            return None

        package_manager = workspace_profile.get("package_manager") or "npm"
        if package_manager == "pnpm":
            return "pnpm install --frozen-lockfile"
        if package_manager == "yarn":
            return "yarn install --frozen-lockfile"
        return "npm install"

    def _run_script_command(self, workspace_profile: dict, script_name: str) -> str:
        package_manager = workspace_profile.get("package_manager") or "npm"
        if package_manager == "pnpm":
            return f"pnpm run {script_name}"
        if package_manager == "yarn":
            return f"yarn {script_name}"
        return f"npm run {script_name}"
