"""
fixpilot/sandbox.py

A Sandbox is a throwaway Docker container that the agent runs commands in.
Our Python code (the "brain") stays on the laptop; only commands go inside.

Usage:
    with Sandbox("swebench/sweb.eval.x86_64.django_1776_django-11099:latest") as sb:
        result = sb.run("ls")
        print(result.output, result.exit_code)
        patch = sb.diff()
"""

import time
import uuid
from dataclasses import dataclass

import docker
from docker.errors import NotFound

# SWE-bench images keep the repo's Python environment in a conda env called "testbed".
# Every agent command switches it on first, so `python` means the repo's own Python.
SWEBENCH_ENV_SETUP = "source /opt/miniconda3/bin/activate && conda activate testbed"

TIMEOUT_EXIT_CODE = 124  # what the `timeout` tool returns when it had to kill a command

# Stop tools from waiting for a keypress (pagers) or flooding the output with progress bars.
NON_INTERACTIVE_ENV = {
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}


@dataclass
class CommandResult:
    output: str      # everything the command printed (normal output + errors, in order)
    exit_code: int   # 0 = success, anything else = error, 124 = killed by the timeout
    duration: float  # seconds the command took

    @property
    def timed_out(self) -> bool:
        return self.exit_code == TIMEOUT_EXIT_CODE


class Sandbox:
    def __init__(
        self,
        image: str,
        workdir: str = "/testbed",
        env_setup: str = SWEBENCH_ENV_SETUP,
        default_timeout: int = 180,
    ):
        self.image = image
        self.workdir = workdir
        self.env_setup = env_setup
        self.default_timeout = default_timeout
        self.client = docker.from_env()
        self.container = None
        self.base_commit: str | None = None

    # ---------- lifecycle: start / stop ----------

    def start(self) -> None:
        """Start a container in the background, keep it alive, remember the base commit."""
        self.container = self.client.containers.run(
            self.image,
            command="sleep infinity",              # keeps the container running until we delete it
            name=f"fixpilot-{uuid.uuid4().hex[:8]}",
            working_dir=self.workdir,
            network_mode="none",                   # no internet: the agent can't look up the real fix
            detach=True,                           # run in the background, give control back to Python
        )
        self.run(f"git config --global --add safe.directory {self.workdir}", use_env=False)
        result = self.run("git rev-parse HEAD", use_env=False, timeout=30)
        if result.exit_code != 0:
            raise RuntimeError(f"Could not read the base commit:\n{result.output}")
        self.base_commit = result.output.strip()

    def stop(self) -> None:
        """Delete the container, and with it everything the agent changed inside."""
        if self.container is not None:
            try:
                self.container.remove(force=True)
            except NotFound:
                pass
            self.container = None

    def __enter__(self) -> "Sandbox":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()  # runs even if the code inside the `with` block crashed

    # ---------- actions: run / diff ----------

    def run(self, command: str, timeout: int | None = None, use_env: bool = True) -> CommandResult:
        """Run one shell command inside the container and return what it printed."""
        if self.container is None:
            raise RuntimeError("Sandbox not started. Use `with Sandbox(...) as sb:` or call start().")
        timeout = timeout or self.default_timeout
        script = f"{self.env_setup}\n{command}" if (use_env and self.env_setup) else command
        started = time.monotonic()
        exit_code, raw = self.container.exec_run(
            ["timeout", "-k", "5", str(timeout), "bash", "-c", script],
            workdir=self.workdir,
            environment=NON_INTERACTIVE_ENV,
        )
        return CommandResult(
            output=raw.decode("utf-8", errors="replace") if raw else "",
            exit_code=exit_code,
            duration=round(time.monotonic() - started, 2),
        )

    def diff(self) -> str:
        """The agent's changes as a patch, compared to the base commit.

        Note: only files git already tracks are included. Brand-new files the agent
        creates (like a scratch reproduce.py) are left out on purpose.
        """
        result = self.run(f"git -c core.fileMode=false diff {self.base_commit}", use_env=False, timeout=60)
        if result.exit_code != 0:
            raise RuntimeError(f"git diff failed:\n{result.output}")
        return result.output


# ---------- self-test: `uv run python -m fixpilot.sandbox` ----------

def _show(title: str, r: CommandResult) -> None:
    print(f"\n--- {title} [exit {r.exit_code}, {r.duration}s] ---")
    print(r.output.rstrip() or "(no output)")


if __name__ == "__main__":
    IMAGE = "swebench/sweb.eval.x86_64.django_1776_django-11099:latest"

    with Sandbox(IMAGE) as sb:
        print("container:", sb.container.name)
        print("base commit:", sb.base_commit)

        _show("1. where am I, which python", sb.run("pwd && python --version"))
        _show("2. a failing command", sb.run("ls /does-not-exist"))
        _show("3. internet blocked?", sb.run("getent hosts github.com || echo 'no network (expected)'"))
        _show("3b. pager disabled?", sb.run("echo PAGER=$PAGER GIT_PAGER=$GIT_PAGER && git log -1 --oneline"))

        r = sb.run("sleep 10", timeout=3)
        _show("4. timeout test (sleep 10, limit 3s)", r)
        print("timed out:", r.timed_out)

        sb.run("echo '# fixpilot test edit' >> django/contrib/auth/validators.py")
        print("\n--- 5. diff after a small edit ---")
        print(sb.diff())

    print("container removed:", sb.container is None)
