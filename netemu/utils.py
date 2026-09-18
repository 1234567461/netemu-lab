"""Shared helpers: shell execution, naming, logging."""

from __future__ import annotations

import shlex
import subprocess
from typing import Sequence


class CommandError(RuntimeError):
    """Raised when a local shell command fails."""

    def __init__(self, cmd: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"Command failed ({returncode}): {' '.join(shlex.quote(c) for c in cmd)}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )


def run(cmd: Sequence[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command, optionally raising on non-zero exit."""
    proc = subprocess.run(
        list(cmd),
        text=True,
        capture_output=capture,
    )
    if check and proc.returncode != 0:
        raise CommandError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def container_name(prefix: str, node: str) -> str:
    """Return the Docker container name for a node."""
    return f"{prefix}-{node}"


def network_name(prefix: str, link_index: int) -> str:
    """Return the Docker network name for a point-to-point link."""
    return f"{prefix}-link-{link_index:03d}"


def short_id(name: str) -> str:
    """Normalize a name for use as an interface suffix."""
    return "".join(ch if ch.isalnum() else "_" for ch in name)[:12]
