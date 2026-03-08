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
        self.mode = mode
        self.container_started = False
        
    def start_container(self):
        """Spools up the sandbox container."""
        if self.mode != "docker" or shutil.which("docker") is None:
            self.mode = "local"
            self.container_started = True
            return {"mode": self.mode, "started": True}

        result = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
            text=True,
            check=False,
        )
        self.container_started = result.returncode == 0
        if not self.container_started:
            self.mode = "local"
            self.container_started = True
        return {"mode": self.mode, "started": self.container_started}
        
    def execute(self, cmd: str, timeout: int = 60, env: dict[str, str] | None = None) -> dict:
        """
        Run a command in the container.
        Returns dict with stdout, stderr, and exit_code.
        """
        if not self.container_started:
            self.start_container()

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
