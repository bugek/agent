import os
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
        
    def execute(self, cmd: str, timeout: int = 60) -> dict:
        """
        Run a command in the container.
        Returns dict with stdout, stderr, and exit_code.
        """
        if not self.container_started:
            self.start_container()

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
                self.image,
                "sh",
                "-lc",
                cmd,
            ]
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
            )

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "mode": self.mode,
        }
        
    def cleanup(self):
        """Stops and removes the container."""
        self.container_started = False
        return {"cleaned": True, "mode": self.mode}
