import os
import time
import shutil
import subprocess
import posixpath
import json
from pathlib import Path


def _text_run_kwargs(**overrides):
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    kwargs.update(overrides)
    return kwargs


class SandboxRunner:
    """
    Executes commands inside a safe, isolated container (e.g., Docker).
    """
    
    def __init__(
        self,
        container_image: str,
        workspace_dir: str = ".",
        mode: str = "local",
        compose_file: str | None = None,
        compose_service: str | None = None,
        compose_project_name: str | None = None,
        compose_ready_services: list[str] | None = None,
        compose_readiness_timeout_seconds: int = 30,
    ):
        self.image = container_image
        self.workspace = workspace_dir
        self.mode = (mode or "auto").lower()
        self.requested_mode = self.mode
        self.compose_file = compose_file
        self.compose_service = compose_service
        self.compose_project_name = compose_project_name
        self.compose_ready_services = [service for service in (compose_ready_services or []) if isinstance(service, str) and service]
        self.compose_readiness_timeout_seconds = max(1, int(compose_readiness_timeout_seconds or 30))
        self.startup_details = {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.mode,
            "started": False,
            "fallback_reason": None,
            "docker_available": False,
            "image_available": False,
            "compose_file": self.compose_file,
            "compose_service": self.compose_service,
            "compose_project_name": self._resolved_compose_project_name(),
            "compose_readiness_status": None,
            "compose_ready_services": self._effective_ready_services(),
            "compose_logs_path": None,
        }
        self.container_started = False
        self.compose_stack_started = False
        
    def start_container(self):
        """Spools up the sandbox container."""
        docker_available = shutil.which("docker") is not None
        self.startup_details = {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.mode,
            "started": False,
            "fallback_reason": None,
            "docker_available": docker_available,
            "image_available": False,
            "compose_file": self.compose_file,
            "compose_service": self.compose_service,
            "compose_project_name": self._resolved_compose_project_name(),
            "compose_readiness_status": None,
            "compose_ready_services": self._effective_ready_services(),
            "compose_logs_path": None,
        }
        self.compose_stack_started = False

        if self.requested_mode == "local":
            self.mode = "local"
            self.container_started = True
            self.startup_details.update({"resolved_mode": "local", "started": True})
            return dict(self.startup_details)

        if self.requested_mode not in {"auto", "docker", "docker_required"}:
            if self.requested_mode in {"compose", "compose_required"}:
                return self._start_compose_backend(docker_available)
            self.mode = "local"
            self.container_started = True
            self.startup_details.update(
                {
                    "resolved_mode": "local",
                    "started": True,
                    "fallback_reason": f"unsupported_mode:{self.requested_mode}",
                }
            )
            return dict(self.startup_details)

        if not docker_available:
            if self.requested_mode == "docker_required":
                self.mode = "docker_required"
                self.container_started = False
                self.startup_details.update(
                    {
                        "resolved_mode": "unavailable",
                        "started": False,
                        "fallback_reason": "docker_unavailable",
                    }
                )
                return dict(self.startup_details)

            self.mode = "local"
            self.container_started = True
            self.startup_details.update(
                {
                    "resolved_mode": "local",
                    "started": True,
                    "fallback_reason": "docker_unavailable",
                }
            )
            return dict(self.startup_details)

        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
            **_text_run_kwargs(),
        )
        image_available = result.returncode == 0
        self.startup_details["image_available"] = image_available
        self.container_started = image_available
        if not image_available:
            if self.requested_mode == "docker_required":
                self.mode = "docker_required"
                self.container_started = False
                self.startup_details.update(
                    {
                        "resolved_mode": "unavailable",
                        "started": False,
                        "fallback_reason": "docker_image_missing",
                    }
                )
                return dict(self.startup_details)

            self.mode = "local"
            self.container_started = True
            self.startup_details.update(
                {
                    "resolved_mode": "local",
                    "started": True,
                    "fallback_reason": "docker_image_missing",
                }
            )
            return dict(self.startup_details)

        self.mode = "docker"
        self.startup_details.update({"resolved_mode": "docker", "started": True})
        return dict(self.startup_details)

    def _start_compose_backend(self, docker_available: bool) -> dict[str, object]:
        if not docker_available:
            return self._compose_fallback("docker_unavailable")

        compose_file = self._resolved_compose_file()
        if compose_file is None:
            return self._compose_fallback("compose_file_missing")
        if not self.compose_service:
            return self._compose_fallback("compose_service_missing")

        version_result = subprocess.run(["docker", "compose", "version"], **_text_run_kwargs(cwd=self.workspace))
        if version_result.returncode != 0:
            return self._compose_fallback("docker_compose_unavailable")

        up_result = subprocess.run(
            [*self._compose_base_command(compose_file), "up", "-d"],
            **_text_run_kwargs(cwd=self.workspace),
        )
        if up_result.returncode != 0:
            return self._compose_fallback("compose_start_failed")
        self.compose_stack_started = True

        readiness = self._wait_for_compose_services(compose_file)
        if readiness["status"] != "ready":
            self.startup_details.update(
                {
                    "compose_readiness_status": readiness["status"],
                    "compose_ready_services": readiness["services"],
                    "compose_logs_path": self.capture_compose_logs(),
                }
            )
            return self._compose_fallback("compose_service_not_ready")

        self.mode = "compose"
        self.container_started = True
        self.startup_details.update(
            {
                "resolved_mode": "compose",
                "started": True,
                "compose_file": compose_file,
                "compose_service": self.compose_service,
                "compose_project_name": self._resolved_compose_project_name(),
                "compose_readiness_status": readiness["status"],
                "compose_ready_services": readiness["services"],
            }
        )
        return dict(self.startup_details)

    def _compose_fallback(self, reason: str) -> dict[str, object]:
        if self.requested_mode == "compose_required":
            self.mode = "compose_required"
            self.container_started = False
            self.startup_details.update(
                {
                    "resolved_mode": "unavailable",
                    "started": False,
                    "fallback_reason": reason,
                }
            )
            return dict(self.startup_details)

        self.mode = "local"
        self.container_started = True
        self.startup_details.update(
            {
                "resolved_mode": "local",
                "started": True,
                "fallback_reason": reason,
            }
        )
        return dict(self.startup_details)

    def probe(self) -> dict[str, object]:
        startup = self.start_container()
        fallback_reason = startup.get("fallback_reason") if isinstance(startup, dict) else None
        recommendation = None
        if fallback_reason == "docker_unavailable":
            recommendation = "Install Docker Desktop or set SANDBOX_MODE=local when Docker is not required."
        elif fallback_reason == "docker_image_missing":
            recommendation = f"Build the sandbox image with: docker build -t {self.image} ."
        elif fallback_reason == "compose_file_missing":
            recommendation = "Set SANDBOX_COMPOSE_FILE to a docker-compose.yml path within the workspace."
        elif fallback_reason == "compose_service_missing":
            recommendation = "Set SANDBOX_COMPOSE_SERVICE to the service that should execute validation commands."
        elif fallback_reason == "docker_compose_unavailable":
            recommendation = "Install Docker Compose v2 support or use SANDBOX_MODE=local when multi-service validation is not required."
        elif fallback_reason == "compose_service_not_ready":
            recommendation = "Inspect the captured compose logs and service readiness configuration before rerunning validation."

        return {
            **startup,
            "image": self.image,
            "docker_sandbox_ready": startup.get("resolved_mode") in {"docker", "compose"},
            "degraded": bool(fallback_reason),
            "recommendation": recommendation,
        }
        
    def execute(self, cmd: str, timeout: int = 60, env: dict[str, str] | None = None) -> dict:
        """
        Run a command in the container.
        Returns dict with stdout, stderr, and exit_code.
        """
        if not self.container_started:
            startup = self.start_container()
            if not startup.get("started", False):
                return {
                    "stdout": "",
                    "stderr": f"Sandbox backend unavailable: {startup.get('fallback_reason') or 'startup_failed'}",
                    "exit_code": 125,
                    "mode": startup.get("resolved_mode") or self.mode,
                    "duration_ms": 0,
                    "timed_out": False,
                }

        runtime_env = os.environ.copy()
        if env:
            runtime_env.update(env)

        started_at = time.perf_counter()
        timed_out = False

        try:
            if self.mode == "docker":
                workspace = os.path.abspath(self.workspace)
                docker_env = self._docker_environment(env, workspace)
                docker_cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{workspace}:/workspace",
                    "-w",
                    "/workspace",
                ]
                if docker_env:
                    for key, value in docker_env.items():
                        docker_cmd.extend(["-e", f"{key}={value}"])
                docker_cmd.extend([
                    self.image,
                    "sh",
                    "-lc",
                    cmd,
                ])
                result = subprocess.run(docker_cmd, **_text_run_kwargs(timeout=timeout))
            elif self.mode == "compose":
                workspace = os.path.abspath(self.workspace)
                compose_file = self._resolved_compose_file()
                compose_env = self._docker_environment(env, workspace)
                compose_cmd = [*self._compose_base_command(compose_file), "exec", "-T"]
                for key, value in compose_env.items():
                    compose_cmd.extend(["-e", f"{key}={value}"])
                compose_cmd.extend([
                    self.compose_service or "",
                    "sh",
                    "-lc",
                    cmd,
                ])
                result = subprocess.run(compose_cmd, **_text_run_kwargs(timeout=timeout, cwd=self.workspace))
            else:
                result = subprocess.run(
                    cmd,
                    cwd=self.workspace,
                    shell=True,
                    env=runtime_env,
                    **_text_run_kwargs(timeout=timeout),
                )
            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + f"\nCommand timed out after {timeout} seconds."
            exit_code = 124

        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "mode": self.mode,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
        }

    def _docker_environment(self, env: dict[str, str] | None, workspace: str) -> dict[str, str]:
        if not env:
            return {}

        translated: dict[str, str] = {}
        normalized_workspace = os.path.normcase(os.path.normpath(workspace))

        for key, value in env.items():
            translated[key] = self._containerize_path_value(value, workspace, normalized_workspace)

        return translated

    def _containerize_path_value(self, value: str, workspace: str, normalized_workspace: str) -> str:
        if not isinstance(value, str) or not value:
            return value

        normalized_value = os.path.normcase(os.path.normpath(value))
        workspace_prefix = normalized_workspace + os.sep
        if normalized_value != normalized_workspace and not normalized_value.startswith(workspace_prefix):
            return value

        relative_path = os.path.relpath(os.path.normpath(value), workspace)
        if relative_path in {".", ""}:
            return "/workspace"

        return posixpath.join("/workspace", relative_path.replace("\\", "/"))
        
    def cleanup(self):
        """Stops and removes the container."""
        if self.mode == "compose" and self.container_started:
            compose_file = self._resolved_compose_file()
            subprocess.run(
                [*self._compose_base_command(compose_file), "down", "--remove-orphans"],
                **_text_run_kwargs(cwd=self.workspace),
            )
        elif self.compose_stack_started:
            compose_file = self._resolved_compose_file()
            subprocess.run(
                [*self._compose_base_command(compose_file), "down", "--remove-orphans"],
                **_text_run_kwargs(cwd=self.workspace),
            )
        self.container_started = False
        self.compose_stack_started = False
        return {"cleaned": True, "mode": self.mode}

    def capture_compose_logs(self) -> str | None:
        compose_file = self._resolved_compose_file()
        if self.requested_mode not in {"compose", "compose_required"} or compose_file is None:
            return None

        logs_result = subprocess.run(
            [*self._compose_base_command(compose_file), "logs", "--no-color"],
            **_text_run_kwargs(cwd=self.workspace),
        )
        logs_text = f"{logs_result.stdout}{logs_result.stderr}".strip()
        if not logs_text:
            return None

        logs_dir = Path(self.workspace) / ".ai-code-agent" / "compose"
        logs_dir.mkdir(parents=True, exist_ok=True)
        logs_path = logs_dir / f"{self._resolved_compose_project_name()}-logs.txt"
        logs_path.write_text(logs_text + "\n", encoding="utf-8")
        return logs_path.relative_to(Path(self.workspace)).as_posix()

    def _resolved_compose_file(self) -> str | None:
        if not self.compose_file:
            return None
        candidate = Path(self.compose_file)
        if not candidate.is_absolute():
            candidate = Path(self.workspace) / candidate
        if not candidate.exists():
            return None
        return str(candidate)

    def _resolved_compose_project_name(self) -> str:
        if isinstance(self.compose_project_name, str) and self.compose_project_name.strip():
            return self.compose_project_name.strip()
        return Path(os.path.abspath(self.workspace)).name.replace("_", "-")

    def _effective_ready_services(self) -> list[str]:
        services = list(self.compose_ready_services)
        if not services and isinstance(self.compose_service, str) and self.compose_service:
            services.append(self.compose_service)
        return services

    def _wait_for_compose_services(self, compose_file: str) -> dict[str, object]:
        services = self._effective_ready_services()
        if not services:
            return {"status": "not_configured", "services": []}

        deadline = time.time() + self.compose_readiness_timeout_seconds
        while time.time() <= deadline:
            status_result = subprocess.run(
                [*self._compose_base_command(compose_file), "ps", "--format", "json"],
                **_text_run_kwargs(cwd=self.workspace),
            )
            if status_result.returncode == 0 and self._compose_services_ready(status_result.stdout, services):
                return {"status": "ready", "services": services}
            time.sleep(1)

        return {"status": "timed_out", "services": services}

    def _compose_services_ready(self, raw_output: str, services: list[str]) -> bool:
        parsed = self._parse_compose_ps_output(raw_output)
        if not parsed:
            return False
        service_statuses = {
            item.get("Service") or item.get("Name") or item.get("service") or item.get("name"): item
            for item in parsed
            if isinstance(item, dict)
        }
        for service in services:
            details = service_statuses.get(service)
            if not isinstance(details, dict):
                return False
            state = str(details.get("State") or details.get("state") or "").lower()
            health = str(details.get("Health") or details.get("health") or "").lower()
            if state not in {"running"}:
                return False
            if health and health not in {"healthy", "running"}:
                return False
        return True

    def _parse_compose_ps_output(self, raw_output: str) -> list[dict[str, object]]:
        text = raw_output.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            entries: list[dict[str, object]] = []
            for line in text.splitlines():
                candidate = line.strip()
                if not candidate:
                    continue
                try:
                    parsed_line = json.loads(candidate)
                except json.JSONDecodeError:
                    return []
                if isinstance(parsed_line, dict):
                    entries.append(parsed_line)
            return entries
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []

    def _compose_base_command(self, compose_file: str | None) -> list[str]:
        command = ["docker", "compose"]
        if compose_file:
            command.extend(["-f", compose_file])
        project_name = self._resolved_compose_project_name()
        if project_name:
            command.extend(["-p", project_name])
        return command
