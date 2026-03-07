from pathlib import Path
import shutil
import subprocess

class LinterTool:
    """Wrapper to run linters/formatters like Ruff, ESLint."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, cwd=self.workspace, capture_output=True, text=True, check=False)
        
    def run_linter(self, file_path: str) -> str:
        """Runs the registered linter on a file and returns the output."""
        suffix = Path(file_path).suffix.lower()
        if suffix in {".js", ".jsx", ".ts", ".tsx"}:
            eslint = self._find_eslint()
            if eslint is None:
                return ""
            result = self._run([eslint, file_path])
        elif shutil.which("ruff"):
            result = self._run(["ruff", "check", file_path])
        else:
            result = self._run(["python", "-m", "py_compile", file_path])
        return (result.stdout + result.stderr).strip()
        
    def apply_formatter(self, file_path: str) -> bool:
        """Auto-formats the file if possible."""
        if not shutil.which("ruff"):
            return False
        result = self._run(["ruff", "format", file_path])
        return result.returncode == 0

    def _find_eslint(self) -> str | None:
        local_bins = [
            Path(self.workspace) / "node_modules" / ".bin" / "eslint.cmd",
            Path(self.workspace) / "node_modules" / ".bin" / "eslint",
        ]
        for candidate in local_bins:
            if candidate.exists():
                return str(candidate)
        return shutil.which("eslint")
