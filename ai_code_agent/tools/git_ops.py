import subprocess

class GitOps:
    """Wrapper for executing git operations locally."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.workspace, capture_output=True, text=True, check=False)
        
    def current_branch(self) -> str:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to determine current branch")
        return result.stdout.strip()
        
    def create_branch(self, branch_name: str) -> bool:
        result = self._run(["checkout", "-b", branch_name])
        return result.returncode == 0
        
    def commit_changes(self, message: str) -> bool:
        add_result = self._run(["add", "-A"])
        if add_result.returncode != 0:
            return False
        commit_result = self._run(["commit", "-m", message])
        return commit_result.returncode == 0
        
    def push_branch(self, branch_name: str) -> bool:
        result = self._run(["push", "-u", "origin", branch_name])
        return result.returncode == 0
