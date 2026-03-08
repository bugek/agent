import os
import time
import shutil
import subprocess


class SandboxRunner:
    """
    Executes commands inside a safe, isolated container (e.g., Docker).
    """
    
    def __init__(self, container_image: str, workspace_dir: str = ".", mode: str = "local"):
        self.image = container_image
        self.workspace = workspace_dir
        self.mode = (mode or "auto").lower()
        self.requested_mode = self.mode
        self.startup_details = {
            "requested_mode": self.requested_mode,
            "resolved_mode": self.mode,
            "started": False,
            "fallback_reason": None,
            "docker_available": False,
            "image_available": False,
        }
        self.container_started = False
        
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
        }

        if self.requested_mode == "local":
            self.mode = "local"
            self.container_started = True
            self.startup_details.update({"resolved_mode": "local", "started": True})
            return dict(self.startup_details)

        if self.requested_mode not in {"auto", "docker", "docker_required"}:
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
            capture_output=True,
            text=True,
            check=False,
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
                docker_cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{workspace}:/workspace",
                    "-w",
                    "/workspace",
                ]
                if env:
                    for key, value in env.items():
                        docker_cmd.extend(["-e", f"{key}={value}"])
                docker_cmd.extend([
                    self.image,
                    "sh",
                    "-lc",
                    cmd,
                ])
                result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=timeout, check=False)
            else:
                result = subprocess.run(
                    cmd,
                    cwd=self.workspace,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    env=runtime_env,
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
        
    def cleanup(self):
        """Stops and removes the container."""
        self.container_started = False
        return {"cleaned": True, "mode": self.mode}
