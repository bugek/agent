from pathlib import Path
from typing import Optional

class FileEditor:
    """Agent-Computer Interface for editing files safely."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir

    def _resolve_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if path.is_absolute():
            return path
        return Path(self.workspace) / path

    def exists(self, file_path: str) -> bool:
        """Return whether a file exists inside the workspace."""
        return self._resolve_path(file_path).exists()

    def ensure_parent(self, file_path: str) -> Path:
        """Ensure the parent directory exists and return the resolved path."""
        path = self._resolve_path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
        
    def view_file(self, file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        """Returns the content of a file, optionally bounded by line numbers."""
        path = self._resolve_path(file_path)
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        if start_line is None and end_line is None:
            return "".join(lines)
        start_index = 0 if start_line is None else max(start_line - 1, 0)
        end_index = len(lines) if end_line is None else min(end_line, len(lines))
        return "".join(lines[start_index:end_index])
        
    def replace_lines(self, file_path: str, start_line: int, end_line: int, new_content: str) -> bool:
        """Replace a block of lines with new content."""
        path = self._resolve_path(file_path)
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        start_index = max(start_line - 1, 0)
        end_index = min(end_line, len(lines))
        replacement = new_content.splitlines(keepends=True)
        lines[start_index:end_index] = replacement
        with path.open("w", encoding="utf-8") as handle:
            handle.writelines(lines)
        return True

    def replace_text(self, file_path: str, old_text: str, new_text: str) -> bool:
        """Replace an exact text block in a file."""
        path = self._resolve_path(file_path)
        content = path.read_text(encoding="utf-8")
        if old_text not in content:
            return False
        path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return True

    def write_file(self, file_path: str, content: str) -> bool:
        """Write a file, creating parent directories as needed."""
        path = self.ensure_parent(file_path)
        path.write_text(content, encoding="utf-8")
        return True

    def insert_lines(self, file_path: str, line_number: int, new_content: str) -> bool:
        """Insert content before the requested 1-based line number."""
        path = self._resolve_path(file_path)
        with path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
        insert_index = max(line_number - 1, 0)
        lines[insert_index:insert_index] = new_content.splitlines(keepends=True)
        with path.open("w", encoding="utf-8") as handle:
            handle.writelines(lines)
        return True
        
    def create_file(self, file_path: str, content: str) -> bool:
        """Create a new file with content."""
        path = self.ensure_parent(file_path)
        if path.exists():
            return False
        path.write_text(content, encoding="utf-8")
        return True

    def delete_file(self, file_path: str) -> bool:
        """Delete a file if it exists."""
        path = self._resolve_path(file_path)
        if not path.exists() or not path.is_file():
            return False
        path.unlink()
        return True
