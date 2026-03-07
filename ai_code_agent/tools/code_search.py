import os
import re
import subprocess
from typing import List

class CodeSearch:
    """Wrapper to search the codebase."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir

    def _run_rg(self, pattern: str) -> List[str]:
        try:
            result = subprocess.run(
                ["rg", "--line-number", "--color", "never", pattern, self.workspace],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return []
        if result.returncode not in (0, 1):
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _walk_files(self) -> List[str]:
        files: List[str] = []
        for root, _, file_names in os.walk(self.workspace):
            if ".git" in root.split(os.sep):
                continue
            for file_name in file_names:
                files.append(os.path.join(root, file_name))
        return files

    def _relative(self, path: str) -> str:
        return os.path.relpath(path, self.workspace)
        
    def search_symbol(self, symbol_name: str) -> List[str]:
        """Search for a class or function name."""
        matches = self._run_rg(rf"(class|def)\s+{re.escape(symbol_name)}\b")
        if matches:
            return matches

        fallback: List[str] = []
        pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
        for file_path in self._walk_files():
            if not file_path.endswith(".py"):
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if pattern.search(line):
                            fallback.append(f"{self._relative(file_path)}:{line_number}:{line.strip()}")
            except (OSError, UnicodeDecodeError):
                continue
        return fallback
        
    def search_text(self, text: str) -> List[str]:
        """Search for any literal text."""
        matches = self._run_rg(re.escape(text))
        if matches:
            return matches

        fallback: List[str] = []
        for file_path in self._walk_files():
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if text.lower() in line.lower():
                            fallback.append(f"{self._relative(file_path)}:{line_number}:{line.strip()}")
            except (OSError, UnicodeDecodeError):
                continue
        return fallback
        
    def list_files(self, directory: str = "") -> List[str]:
        """List files in the workspace matching criteria."""
        base_dir = os.path.join(self.workspace, directory) if directory else self.workspace
        if not os.path.isdir(base_dir):
            return []
        results: List[str] = []
        for root, _, file_names in os.walk(base_dir):
            if ".git" in root.split(os.sep):
                continue
            for file_name in file_names:
                results.append(self._relative(os.path.join(root, file_name)))
        return sorted(results)
