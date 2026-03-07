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
    source_root = "src" if (root / "src").exists() else "."
    main_file = f"{source_root}/main.ts"
    app_module_file = f"{source_root}/app.module.ts"
    module_files = _collect_suffix_files(root, source_root, ".module.ts")
    controller_files = _collect_suffix_files(root, source_root, ".controller.ts")
    service_files = _collect_suffix_files(root, source_root, ".service.ts")
    dto_files = _collect_relative_files(root, source_root, ["dto.ts"])
    dto_files.extend(_collect_glob_files(root, source_root, "dto/*.ts"))
    dto_files.extend(_collect_glob_files(root, source_root, "**/dto/*.ts"))
    entity_files = _collect_suffix_files(root, source_root, ".entity.ts")
    guard_files = _collect_suffix_files(root, source_root, ".guard.ts")
    pipe_files = _collect_suffix_files(root, source_root, ".pipe.ts")
    interceptor_files = _collect_suffix_files(root, source_root, ".interceptor.ts")
    middleware_files = _collect_suffix_files(root, source_root, ".middleware.ts")
    feature_directories = _collect_nest_feature_directories(
        module_files,
        controller_files,
        service_files,
        dto_files,
    )
    priority_files = [
        "package.json",
        "nest-cli.json",
        "tsconfig.json",
        "tsconfig.build.json",
        main_file,
        app_module_file,
        *module_files[:6],
        *controller_files[:6],
        *service_files[:6],
        *dto_files[:4],
    ]
    return {
        "source_root": source_root,
        "has_typescript": "typescript" in dependencies,
        "has_nest_cli": "@nestjs/cli" in dependencies,
        "tsconfig_build": "tsconfig.build.json" if (root / "tsconfig.build.json").exists() else None,
        "main_file": main_file if (root / main_file).exists() else None,
        "app_module_file": app_module_file if (root / app_module_file).exists() else None,
        "module_files": sorted(set(module_files)),
        "controller_files": sorted(set(controller_files)),
        "service_files": sorted(set(service_files)),
        "dto_files": sorted(set(dto_files)),
        "entity_files": sorted(set(entity_files)),
        "guard_files": sorted(set(guard_files)),
        "pipe_files": sorted(set(pipe_files)),
        "interceptor_files": sorted(set(interceptor_files)),
        "middleware_files": sorted(set(middleware_files)),
        "feature_directories": feature_directories,
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


def _collect_suffix_files(root: Path, relative_dir: str | None, suffix: str) -> list[str]:
    if relative_dir is None:
        return []
    base = root / relative_dir
    if not base.exists():
        return []
    results: list[str] = []
    for file_path in base.rglob(f"*{suffix}"):
        if file_path.is_file():
            results.append(file_path.relative_to(root).as_posix())
    return sorted(results)


def _collect_glob_files(root: Path, relative_dir: str | None, pattern: str) -> list[str]:
    if relative_dir is None:
        return []
    base = root / relative_dir
    if not base.exists():
        return []
    results: list[str] = []
    for file_path in base.glob(pattern):
        if file_path.is_file():
            results.append(file_path.relative_to(root).as_posix())
    return sorted(results)


def _collect_nest_feature_directories(*file_groups: list[str]) -> list[str]:
    feature_directories: list[str] = []
    seen: set[str] = set()
    for file_group in file_groups:
        for file_path in file_group:
            directory = str(Path(file_path).parent).replace("\\", "/")
            if directory in {".", "src/dto", "src/common", "src"}:
                continue
            if directory not in seen:
                feature_directories.append(directory)
                seen.add(directory)
    return sorted(feature_directories)


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