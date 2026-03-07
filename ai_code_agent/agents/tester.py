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
        sandbox = SandboxRunner(
            container_image=self.config.docker_image,
            workspace_dir=state["workspace_dir"],
            mode=self.config.sandbox_mode,
        )
        sandbox.start_container()

        compile_result = sandbox.execute("python -m compileall ai_code_agent", timeout=120)
        cli_result = sandbox.execute("python -m ai_code_agent.main --help", timeout=120)

        lint_output = []
        linter = LinterTool(state["workspace_dir"])
        for file_path in state.get("files_to_edit", [])[:10]:
            if not file_path.endswith(".py"):
                continue
            result = linter.run_linter(file_path)
            if result:
                lint_output.append(f"[{file_path}] {result}")

        sandbox.cleanup()
        test_passed = compile_result["exit_code"] == 0 and cli_result["exit_code"] == 0
        combined_output = [
            f"compileall(exit={compile_result['exit_code']}):\n{compile_result['stdout']}{compile_result['stderr']}",
            f"cli-help(exit={cli_result['exit_code']}):\n{cli_result['stdout']}{cli_result['stderr']}",
        ]
        if lint_output:
            combined_output.append("lint:\n" + "\n".join(lint_output))

        return {
            "test_passed": test_passed,
            "test_results": "\n\n".join(combined_output).strip(),
        }
