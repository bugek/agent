import subprocess


def _git_run_kwargs() -> dict[str, object]:
    return {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }

class GitOps:
    """Wrapper for executing git operations locally."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.workspace, **_git_run_kwargs())

    def is_repository(self) -> bool:
        result = self._run(["rev-parse", "--is-inside-work-tree"])
        return result.returncode == 0 and result.stdout.strip() == "true"
        
    def current_branch(self) -> str:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to determine current branch")
        return result.stdout.strip()

    def branch_exists(self, branch_name: str) -> bool:
        result = self._run(["show-ref", "--verify", f"refs/heads/{branch_name}"])
        return result.returncode == 0

    def remote_branch_exists(self, branch_name: str) -> bool:
        result = self._run(["ls-remote", "--heads", "origin", f"refs/heads/{branch_name}"])
        return result.returncode == 0 and bool(result.stdout.strip())

    def remote_url(self, remote_name: str = "origin") -> str | None:
        result = self._run(["remote", "get-url", remote_name])
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
        return remote_url or None

    def branches_share_history(self, base_branch: str, branch_name: str) -> bool:
        result = self._run(["merge-base", base_branch, branch_name])
        return result.returncode == 0 and bool(result.stdout.strip())
        
    def create_branch(self, branch_name: str) -> bool:
        current = self.current_branch()
        if current == branch_name:
            return True
        if self.branch_exists(branch_name):
            result = self._run(["checkout", branch_name])
            return result.returncode == 0
        result = self._run(["checkout", "-b", branch_name])
        return result.returncode == 0

    def has_pending_changes(self) -> bool:
        result = self._run(["status", "--porcelain"])
        return result.returncode == 0 and bool(result.stdout.strip())
        
    def commit_changes(self, message: str) -> bool:
        add_result = self._run(["add", "-A"])
        if add_result.returncode != 0:
            return False
        commit_result = self._run(["commit", "-m", message])
        return commit_result.returncode == 0

    def _is_non_fast_forward_push(self, result: subprocess.CompletedProcess[str]) -> bool:
        stderr = (result.stderr or "").lower()
        return any(marker in stderr for marker in ["non-fast-forward", "fetch first", "stale info"])
        
    def push_branch(self, branch_name: str) -> bool:
        result = self._run(["push", "-u", "origin", branch_name])
        if result.returncode != 0 and self._is_non_fast_forward_push(result):
            result = self._run(["push", "--force-with-lease", "-u", "origin", branch_name])
        return result.returncode == 0

    def ensure_remote_base_branch(self, base_branch: str) -> bool:
        current = self.current_branch()
        if self.remote_branch_exists(base_branch):
            if current != base_branch and self.branch_exists(base_branch) and not self.branches_share_history(base_branch, current):
                rebase_result = self._run(["rebase", "--onto", base_branch, "--root"])
                if rebase_result.returncode != 0:
                    self._run(["rebase", "--abort"])
                    return False
            return True

        if self.branch_exists(base_branch):
            checkout_result = self._run(["checkout", base_branch])
            if checkout_result.returncode != 0:
                return False
            push_ok = self.push_branch(base_branch)
            self._run(["checkout", current])
            return push_ok

        if self._run(["checkout", "--orphan", base_branch]).returncode != 0:
            return False
        self._run(["rm", "-rf", "--ignore-unmatch", "."])
        if self._run(["commit", "--allow-empty", "-m", "Initialize repository base branch"]).returncode != 0:
            self._run(["checkout", current])
            return False
        push_ok = self.push_branch(base_branch)
        self._run(["checkout", current])
        if not push_ok:
            return False
        if current != base_branch:
            rebase_result = self._run(["rebase", "--onto", base_branch, "--root"])
            if rebase_result.returncode != 0:
                self._run(["rebase", "--abort"])
                return False
        return True
