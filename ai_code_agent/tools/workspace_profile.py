import json
from pathlib import Path
from typing import Any


def detect_workspace_profile(workspace_dir: str) -> dict[str, Any]:
    root = Path(workspace_dir)
    package_json_path = root / "package.json"
    profile: dict[str, Any] = {
        "has_python": (root / "pyproject.toml").exists(),
        "has_package_json": package_json_path.exists(),
        "frameworks": [],
        "package_manager": None,
        "scripts": [],
        "priority_files": [],
        "source_root": ".",
        "nextjs": None,
        "nestjs": None,
        "tsconfig_exists": (root / "tsconfig.json").exists(),
        "lockfiles": [],
        "needs_install": not (root / "node_modules").exists(),
    }

    if profile["has_python"]:
        profile["frameworks"].append("python")
        profile["priority_files"].append("pyproject.toml")

    if not package_json_path.exists():
        return profile

    package_data = _read_json(package_json_path)
    dependencies = {
        **package_data.get("dependencies", {}),
        **package_data.get("devDependencies", {}),
    }
    profile["scripts"] = sorted(package_data.get("scripts", {}).keys())
    profile["priority_files"].append("package.json")
    profile["source_root"] = "src" if (root / "src").exists() else "."

    if (root / "pnpm-lock.yaml").exists():
        profile["package_manager"] = "pnpm"
        profile["lockfiles"].append("pnpm-lock.yaml")
    elif (root / "yarn.lock").exists():
        profile["package_manager"] = "yarn"
        profile["lockfiles"].append("yarn.lock")
    elif (root / "package-lock.json").exists():
        profile["package_manager"] = "npm"
        profile["lockfiles"].append("package-lock.json")
    else:
        profile["package_manager"] = "npm"

    next_profile = _detect_nextjs_profile(root, dependencies)
    if next_profile is not None:
        profile["frameworks"].append("nextjs")
        profile["nextjs"] = next_profile
        profile["priority_files"].extend(next_profile["priority_files"])

    nest_profile = _detect_nestjs_profile(root, dependencies)
    if nest_profile is not None:
        profile["frameworks"].append("nestjs")
        profile["nestjs"] = nest_profile
        profile["priority_files"].extend(nest_profile["priority_files"])

    profile["priority_files"] = _deduplicate_existing_paths(root, profile["priority_files"])
    return profile


def _detect_nextjs_profile(root: Path, dependencies: dict[str, Any]) -> dict[str, Any] | None:
    config_candidates = ["next.config.js", "next.config.mjs", "next.config.ts"]
    has_next = "next" in dependencies or any((root / name).exists() for name in config_candidates)
    if not has_next:
        return None

    app_dir = _first_existing(root, ["app", "src/app"])
    pages_dir = _first_existing(root, ["pages", "src/pages"])
    router_type = "app" if app_dir is not None else "pages" if pages_dir is not None else "unknown"
    route_root = app_dir or pages_dir

    route_files = _collect_relative_files(root, route_root, ["page.tsx", "page.ts", "page.jsx", "page.js", "index.tsx", "index.ts", "index.jsx", "index.js"])
    layout_files = _collect_relative_files(root, route_root, ["layout.tsx", "layout.ts", "layout.jsx", "layout.js", "_app.tsx", "_app.ts", "_app.jsx", "_app.js", "_document.tsx", "_document.ts", "_document.jsx", "_document.js"])
    special_files = _collect_relative_files(root, route_root, ["loading.tsx", "loading.ts", "error.tsx", "error.ts", "template.tsx", "template.ts", "not-found.tsx", "not-found.ts"])
    api_routes = _collect_api_routes(root, app_dir, pages_dir)
    component_dirs = _existing_relative_dirs(root, ["components", "src/components", "app/components", "src/app/components"])

    priority_files = ["package.json", *config_candidates, *layout_files[:4], *route_files[:6], *special_files[:4]]
    if router_type == "app" and app_dir is not None:
        priority_files.extend([f"{app_dir}/layout.tsx", f"{app_dir}/page.tsx"])
    if router_type == "pages" and pages_dir is not None:
        priority_files.extend([f"{pages_dir}/_app.tsx", f"{pages_dir}/index.tsx"])

    return {
        "router_type": router_type,
        "app_dir": app_dir,
        "pages_dir": pages_dir,
        "config_files": [name for name in config_candidates if (root / name).exists()],
        "route_files": route_files,
        "layout_files": layout_files,
        "special_files": special_files,
        "api_routes": api_routes,
        "component_directories": component_dirs,
        "priority_files": priority_files,
    }


def _detect_nestjs_profile(root: Path, dependencies: dict[str, Any]) -> dict[str, Any] | None:
    if "@nestjs/core" not in dependencies and not (root / "nest-cli.json").exists():
        return None
    priority_files = ["package.json", "nest-cli.json", "src/main.ts", "src/app.module.ts"]
    return {
        "priority_files": _deduplicate_existing_paths(root, priority_files),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _first_existing(root: Path, candidates: list[str]) -> str | None:
    for candidate in candidates:
        if (root / candidate).exists():
            return candidate.replace("\\", "/")
    return None


def _collect_relative_files(root: Path, relative_dir: str | None, names: list[str]) -> list[str]:
    if relative_dir is None:
        return []
    base = root / relative_dir
    if not base.exists():
        return []
    normalized_names = set(names)
    results: list[str] = []
    for file_path in base.rglob("*"):
        if file_path.is_file() and file_path.name in normalized_names:
            results.append(file_path.relative_to(root).as_posix())
    return sorted(results)


def _collect_api_routes(root: Path, app_dir: str | None, pages_dir: str | None) -> list[str]:
    api_routes: list[str] = []
    if app_dir is not None:
        app_root = root / app_dir
        for file_path in app_root.rglob("route.*"):
            if file_path.is_file():
                api_routes.append(file_path.relative_to(root).as_posix())
    if pages_dir is not None:
        api_root = root / pages_dir / "api"
        if api_root.exists():
            for file_path in api_root.rglob("*"):
                if file_path.is_file() and file_path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
                    api_routes.append(file_path.relative_to(root).as_posix())
    return sorted(set(api_routes))


def _existing_relative_dirs(root: Path, directories: list[str]) -> list[str]:
    return [directory.replace("\\", "/") for directory in directories if (root / directory).exists()]


def _deduplicate_existing_paths(root: Path, paths: list[str]) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = path.replace("\\", "/")
        if normalized in seen:
            continue
        if (root / normalized).exists():
            results.append(normalized)
            seen.add(normalized)
    return results