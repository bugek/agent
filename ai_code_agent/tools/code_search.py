import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass
class IndexedFile:
    path: str
    kind: str
    symbols: list[str]
    imports: list[str]
    import_paths: list[str]
    references: list[str]
    path_tokens: list[str]

class CodeSearch:
    """Wrapper to search the codebase."""
    
    def __init__(self, workspace_dir: str):
        self.workspace = workspace_dir
        self._indexed_files: list[IndexedFile] | None = None
        self._import_graph: dict[str, set[str]] | None = None
        self._symbol_graph: dict[str, set[str]] | None = None

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

    def build_index(self) -> list[IndexedFile]:
        if self._indexed_files is not None:
            return self._indexed_files

        indexed_files: list[IndexedFile] = []
        for file_path in self._walk_files():
            relative_path = self._relative(file_path).replace("\\", "/")
            if self._skip_index_file(relative_path):
                continue
            indexed_file = self._index_file(file_path, relative_path)
            if indexed_file is not None:
                indexed_files.append(indexed_file)

        self._indexed_files = indexed_files
        return indexed_files

    def hybrid_search(self, keywords: list[str], workspace_profile: dict | None = None) -> list[tuple[str, int]]:
        normalized_keywords = [keyword.lower() for keyword in keywords if keyword]
        if not normalized_keywords:
            return []

        frameworks = set((workspace_profile or {}).get("frameworks", []))
        scores: dict[str, int] = {}
        for indexed_file in self.build_index():
            score = self._score_indexed_file(indexed_file, normalized_keywords, frameworks)
            if score > 0:
                scores[indexed_file.path] = score

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def related_files(self, target_files: Iterable[str]) -> list[tuple[str, int]]:
        normalized_targets = {target.replace("\\", "/") for target in target_files if target}
        if not normalized_targets:
            return []

        target_tokens: set[str] = set()
        for target in normalized_targets:
            target_tokens.update(self._tokenize_path(target))

        scores: dict[str, int] = {}
        for indexed_file in self.build_index():
            if indexed_file.path in normalized_targets:
                continue
            overlap = target_tokens.intersection(indexed_file.path_tokens)
            if overlap:
                scores[indexed_file.path] = len(overlap) * 2
            import_overlap = target_tokens.intersection(token.lower() for token in indexed_file.imports)
            if import_overlap:
                scores[indexed_file.path] = scores.get(indexed_file.path, 0) + len(import_overlap) * 3

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def build_import_graph(self) -> dict[str, set[str]]:
        if self._import_graph is not None:
            return self._import_graph

        indexed_paths = {indexed_file.path for indexed_file in self.build_index()}
        graph: dict[str, set[str]] = {}
        for indexed_file in self.build_index():
            graph[indexed_file.path] = {
                import_path for import_path in indexed_file.import_paths if import_path in indexed_paths
            }

        self._import_graph = graph
        return graph

    def build_symbol_graph(self) -> dict[str, set[str]]:
        if self._symbol_graph is not None:
            return self._symbol_graph

        symbol_definitions: dict[str, set[str]] = {}
        for indexed_file in self.build_index():
            for symbol in indexed_file.symbols:
                symbol_definitions.setdefault(symbol, set()).add(indexed_file.path)

        graph: dict[str, set[str]] = {indexed_file.path: set() for indexed_file in self.build_index()}
        for indexed_file in self.build_index():
            for reference in indexed_file.references:
                for owner_path in symbol_definitions.get(reference, set()):
                    if owner_path != indexed_file.path:
                        graph[indexed_file.path].add(owner_path)

        self._symbol_graph = graph
        return graph

    def graph_related_files(self, seed_files: Iterable[str], keywords: list[str]) -> list[tuple[str, int]]:
        normalized_seeds = {seed.replace("\\", "/") for seed in seed_files if seed}
        if not normalized_seeds:
            return []

        keyword_set = {keyword.lower() for keyword in keywords if keyword}
        import_graph = self.build_import_graph()
        symbol_graph = self.build_symbol_graph()
        reverse_import_graph = self._reverse_graph(import_graph)
        reverse_symbol_graph = self._reverse_graph(symbol_graph)
        indexed_map = {indexed_file.path: indexed_file for indexed_file in self.build_index()}

        scores: dict[str, int] = {}
        for seed in normalized_seeds:
            neighbor_groups = [
                (self._collect_graph_neighbors(import_graph, seed, max_depth=2), 5),
                (self._collect_graph_neighbors(reverse_import_graph, seed, max_depth=2), 4),
                (self._collect_graph_neighbors(symbol_graph, seed, max_depth=2), 4),
                (self._collect_graph_neighbors(reverse_symbol_graph, seed, max_depth=2), 3),
            ]
            for neighbors, base_score in neighbor_groups:
                for neighbor, depth in neighbors.items():
                    if neighbor in normalized_seeds:
                        continue
                    depth_weight = max(1, 3 - depth)
                    scores[neighbor] = scores.get(neighbor, 0) + base_score * depth_weight
                    indexed_file = indexed_map.get(neighbor)
                    if indexed_file is None:
                        continue
                    keyword_bonus = len(keyword_set.intersection(indexed_file.path_tokens))
                    keyword_bonus += len(keyword_set.intersection(indexed_file.symbols))
                    if keyword_bonus:
                        scores[neighbor] += keyword_bonus * 2

        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    def explain_candidate(self, file_path: str, keywords: list[str], seed_files: Iterable[str] | None = None) -> dict[str, object]:
        normalized_path = file_path.replace("\\", "/")
        indexed_map = {indexed_file.path: indexed_file for indexed_file in self.build_index()}
        indexed_file = indexed_map.get(normalized_path)
        if indexed_file is None:
            return {
                "file_path": normalized_path,
                "kind": "unknown",
                "explanation_edges": [],
                "reasons": [],
            }

        normalized_keywords = [self._normalize_token(keyword) for keyword in keywords if self._normalize_token(keyword)]
        keyword_set = set(normalized_keywords)
        explanation_edges: list[dict[str, object]] = []
        reasons: list[str] = []

        path_overlap = sorted(keyword_set.intersection(indexed_file.path_tokens))
        symbol_overlap = sorted(keyword_set.intersection(indexed_file.symbols))
        import_overlap = sorted(keyword_set.intersection(indexed_file.imports))
        if path_overlap:
            reasons.append(f"path token match: {', '.join(path_overlap)}")
            for token in path_overlap:
                explanation_edges.append(
                    self._build_explanation_edge(
                        edge_type="path_token_match",
                        source_file=None,
                        target_file=normalized_path,
                        direction="keyword_to_file",
                        depth=0,
                        source_keyword=token,
                        target_symbol=token,
                    )
                )
        if symbol_overlap:
            reasons.append(f"symbol match: {', '.join(symbol_overlap)}")
            for token in symbol_overlap:
                explanation_edges.append(
                    self._build_explanation_edge(
                        edge_type="symbol_match",
                        source_file=None,
                        target_file=normalized_path,
                        direction="keyword_to_symbol",
                        depth=0,
                        source_keyword=token,
                        target_symbol=token,
                    )
                )
        if import_overlap:
            reasons.append(f"import token match: {', '.join(import_overlap)}")
            for token in import_overlap:
                explanation_edges.append(
                    self._build_explanation_edge(
                        edge_type="import_token_match",
                        source_file=None,
                        target_file=normalized_path,
                        direction="keyword_to_import",
                        depth=0,
                        source_keyword=token,
                        target_symbol=token,
                    )
                )

        seed_set = {seed.replace('\\', '/') for seed in (seed_files or []) if seed}
        if seed_set:
            import_parents = sorted(seed for seed in seed_set if normalized_path in self.build_import_graph().get(seed, set()))
            imported_seeds = sorted(seed for seed in seed_set if seed in self.build_import_graph().get(normalized_path, set()))
            symbol_parents = sorted(seed for seed in seed_set if normalized_path in self.build_symbol_graph().get(seed, set()))
            symbol_sources = sorted(seed for seed in seed_set if seed in self.build_symbol_graph().get(normalized_path, set()))

            if import_parents:
                reasons.append(f"imported by seed: {', '.join(import_parents[:3])}")
                for seed in import_parents:
                    explanation_edges.append(
                        self._build_explanation_edge(
                            edge_type="imported_by_seed",
                            source_file=seed,
                            target_file=normalized_path,
                            direction="incoming_import",
                            depth=1,
                        )
                    )
            if imported_seeds:
                reasons.append(f"imports seed: {', '.join(imported_seeds[:3])}")
                for seed in imported_seeds:
                    explanation_edges.append(
                        self._build_explanation_edge(
                            edge_type="imports_seed",
                            source_file=normalized_path,
                            target_file=seed,
                            direction="outgoing_import",
                            depth=1,
                        )
                    )
            if symbol_parents:
                reasons.append(f"referenced by seed symbol usage: {', '.join(symbol_parents[:3])}")
                for seed in symbol_parents:
                    explanation_edges.append(
                        self._build_explanation_edge(
                            edge_type="referenced_by_seed_symbol_usage",
                            source_file=seed,
                            target_file=normalized_path,
                            direction="incoming_symbol_reference",
                            depth=1,
                        )
                    )
            if symbol_sources:
                reasons.append(f"defines symbol used by seed: {', '.join(symbol_sources[:3])}")
                for seed in symbol_sources:
                    explanation_edges.append(
                        self._build_explanation_edge(
                            edge_type="defines_symbol_used_by_seed",
                            source_file=normalized_path,
                            target_file=seed,
                            direction="outgoing_symbol_definition",
                            depth=1,
                        )
                    )

        return {
            "file_path": normalized_path,
            "kind": indexed_file.kind,
            "path_overlap": path_overlap,
            "symbol_overlap": symbol_overlap,
            "import_overlap": import_overlap,
            "explanation_edges": self._dedupe_explanation_edges(explanation_edges),
            "reasons": reasons,
        }

    def _build_explanation_edge(
        self,
        edge_type: str,
        source_file: str | None,
        target_file: str,
        direction: str,
        depth: int,
        source_keyword: str | None = None,
        source_symbol: str | None = None,
        target_symbol: str | None = None,
    ) -> dict[str, object]:
        edge: dict[str, object] = {
            "edge_type": edge_type,
            "source_file": source_file,
            "target_file": target_file,
            "direction": direction,
            "depth": depth,
        }
        if source_keyword is not None:
            edge["source_keyword"] = source_keyword
        if source_symbol is not None:
            edge["source_symbol"] = source_symbol
        if target_symbol is not None:
            edge["target_symbol"] = target_symbol
        return edge

    def _dedupe_explanation_edges(self, edges: list[dict[str, object]]) -> list[dict[str, object]]:
        unique: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()
        for edge in edges:
            signature = (
                edge.get("edge_type"),
                edge.get("source_file"),
                edge.get("target_file"),
                edge.get("direction"),
                edge.get("depth"),
                edge.get("source_keyword"),
                edge.get("source_symbol"),
                edge.get("target_symbol"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(edge)
        return unique

    def _index_file(self, absolute_path: str, relative_path: str) -> IndexedFile | None:
        kind = self._classify_file(relative_path)
        if kind == "ignored":
            return None

        content = self._read_text_file(absolute_path)
        if content is None:
            return None

        return IndexedFile(
            path=relative_path,
            kind=kind,
            symbols=self._extract_symbols(relative_path, content),
            imports=self._extract_imports(relative_path, content),
            import_paths=self._extract_import_paths(relative_path, content),
            references=self._extract_references(relative_path, content),
            path_tokens=self._tokenize_path(relative_path),
        )

    def _read_text_file(self, file_path: str) -> str | None:
        try:
            return Path(file_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _extract_symbols(self, relative_path: str, content: str) -> list[str]:
        patterns: list[str] = []
        if relative_path.endswith(".py"):
            patterns = [r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)"]
        elif relative_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            patterns = [
                r"^\s*export\s+class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*export\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*export\s+default\s+function\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)",
                r"^\s*export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)",
            ]

        symbols: list[str] = []
        for pattern in patterns:
            symbols.extend(re.findall(pattern, content, re.M))
        return sorted(set(self._normalize_token(symbol) for symbol in symbols if self._normalize_token(symbol)))

    def _extract_imports(self, relative_path: str, content: str) -> list[str]:
        imports: list[str] = []
        if relative_path.endswith(".py"):
            for imported in re.findall(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import", content, re.M):
                imports.extend(imported.split("."))
            for imported in re.findall(r"^\s*import\s+([A-Za-z0-9_\.,\s]+)", content, re.M):
                imports.extend(part.strip().split(".") for part in imported.split(","))
        elif relative_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            for imported in re.findall(r"from\s+[\"']([^\"']+)[\"']", content):
                imports.extend(self._tokenize_path(imported))
            for imported in re.findall(r"require\([\"']([^\"']+)[\"']\)", content):
                imports.extend(self._tokenize_path(imported))

        flattened: list[str] = []
        for item in imports:
            if isinstance(item, list):
                flattened.extend(item)
            else:
                flattened.append(item)
        return sorted(set(self._normalize_token(token) for token in flattened if self._normalize_token(token)))

    def _extract_import_paths(self, relative_path: str, content: str) -> list[str]:
        import_paths: set[str] = set()
        if relative_path.endswith(".py"):
            module_names = re.findall(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import", content, re.M)
            module_names.extend(re.findall(r"^\s*import\s+([A-Za-z0-9_\.]+)", content, re.M))
            for module_name in module_names:
                resolved = self._resolve_python_module_path(module_name)
                if resolved is not None:
                    import_paths.add(resolved)
        elif relative_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            import_values = re.findall(r"from\s+[\"']([^\"']+)[\"']", content)
            import_values.extend(re.findall(r"require\([\"']([^\"']+)[\"']\)", content))
            for import_value in import_values:
                resolved = self._resolve_script_import_path(relative_path, import_value)
                if resolved is not None:
                    import_paths.add(resolved)
        return sorted(import_paths)

    def _extract_references(self, relative_path: str, content: str) -> list[str]:
        identifiers = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", content)
        stop_tokens = {
            "import", "from", "return", "class", "def", "function", "const", "let", "var",
            "export", "default", "async", "await", "true", "false", "none", "null", "this",
            "self", "for", "while", "if", "else", "elif", "try", "except", "finally", "with",
            "new", "public", "private", "readonly", "type", "interface",
        }
        own_symbols = set(self._extract_symbols(relative_path, content))
        references = {
            self._normalize_token(identifier)
            for identifier in identifiers
            if self._normalize_token(identifier)
            and self._normalize_token(identifier) not in stop_tokens
            and self._normalize_token(identifier) not in own_symbols
            and len(identifier) > 2
        }
        return sorted(references)

    def _score_indexed_file(self, indexed_file: IndexedFile, keywords: list[str], frameworks: set[str]) -> int:
        score = 0
        keyword_set = set(keywords)
        path_overlap = keyword_set.intersection(indexed_file.path_tokens)
        symbol_overlap = keyword_set.intersection(indexed_file.symbols)
        import_overlap = keyword_set.intersection(indexed_file.imports)

        score += len(path_overlap) * 5
        score += len(symbol_overlap) * 6
        score += len(import_overlap) * 3

        if indexed_file.kind in {"config", "entrypoint"}:
            score += 1

        if "nextjs" in frameworks and indexed_file.kind in {"next-route", "next-layout", "next-component", "api-route"}:
            score += 2
        if "nestjs" in frameworks and indexed_file.kind in {"nest-module", "nest-controller", "nest-service", "nest-dto"}:
            score += 2

        return score

    def _tokenize_path(self, path: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_]+", path.replace("\\", "/").lower())
        normalized: list[str] = []
        for token in tokens:
            normalized.extend(
                normalized_part
                for part in re.split(r"[_-]+", token)
                for normalized_part in [self._normalize_token(part)]
                if normalized_part
            )
        return sorted(set(normalized))

    def _normalize_token(self, token: str) -> str:
        normalized = token.strip().lower()
        if not normalized:
            return ""
        if normalized.endswith("ies") and len(normalized) > 4:
            normalized = normalized[:-3] + "y"
        elif normalized.endswith("s") and len(normalized) > 3 and not normalized.endswith("ss"):
            normalized = normalized[:-1]
        return normalized

    def _classify_file(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/").lower()
        file_name = os.path.basename(normalized)

        if normalized.startswith((".git/", "node_modules/", "dist/", "build/", "__pycache__/")):
            return "ignored"
        if file_name in {"package.json", "pyproject.toml", "tsconfig.json", "tsconfig.build.json", "nest-cli.json"}:
            return "config"
        if file_name in {"main.py", "main.ts", "main.js", "app.module.ts", "orchestrator.py"}:
            return "entrypoint"
        if file_name.endswith(".module.ts"):
            return "nest-module"
        if file_name.endswith(".controller.ts"):
            return "nest-controller"
        if file_name.endswith(".service.ts"):
            return "nest-service"
        if file_name.endswith(".dto.ts"):
            return "nest-dto"
        if file_name in {"page.tsx", "page.ts", "index.tsx", "index.ts", "index.jsx", "index.js"}:
            return "next-route"
        if file_name in {"layout.tsx", "layout.ts", "_app.tsx", "_app.ts", "_document.tsx", "_document.ts"}:
            return "next-layout"
        if file_name == "route.ts" or "/api/" in normalized:
            return "api-route"
        if "/components/" in normalized:
            return "next-component"
        if normalized.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
            return "code"
        return "other"

    def _resolve_python_module_path(self, module_name: str) -> str | None:
        candidate = module_name.replace(".", "/")
        for suffix in [".py", "/__init__.py"]:
            resolved = f"{candidate}{suffix}".replace("//", "/")
            if Path(self.workspace, resolved).exists():
                return resolved
        return None

    def _resolve_script_import_path(self, relative_path: str, import_value: str) -> str | None:
        if not import_value.startswith((".", "..")):
            return None

        base_dir = Path(relative_path).parent
        raw_target = (base_dir / import_value).as_posix()
        candidates = [
            raw_target,
            f"{raw_target}.ts",
            f"{raw_target}.tsx",
            f"{raw_target}.js",
            f"{raw_target}.jsx",
            f"{raw_target}/index.ts",
            f"{raw_target}/index.tsx",
            f"{raw_target}/index.js",
            f"{raw_target}/index.jsx",
        ]
        for candidate in candidates:
            normalized = str(Path(candidate)).replace("\\", "/")
            if Path(self.workspace, normalized).exists():
                return normalized
        return None

    def _reverse_graph(self, graph: dict[str, set[str]]) -> dict[str, set[str]]:
        reversed_graph: dict[str, set[str]] = {node: set() for node in graph}
        for source, targets in graph.items():
            reversed_graph.setdefault(source, set())
            for target in targets:
                reversed_graph.setdefault(target, set()).add(source)
        return reversed_graph

    def _collect_graph_neighbors(self, graph: dict[str, set[str]], seed: str, max_depth: int) -> dict[str, int]:
        visited: dict[str, int] = {}
        frontier: list[tuple[str, int]] = [(seed, 0)]
        while frontier:
            node, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for neighbor in graph.get(node, set()):
                next_depth = depth + 1
                best_depth = visited.get(neighbor)
                if best_depth is None or next_depth < best_depth:
                    visited[neighbor] = next_depth
                    frontier.append((neighbor, next_depth))
        return visited

    def _skip_index_file(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        return normalized.startswith((".git/", "node_modules/", "dist/", "build/", "__pycache__/"))
        
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
