"""How a command reaches a target: run here, or over SSH.

SSH is the system's own `ssh`, so its keys, agent, known hosts and
~/.ssh/config all apply, and a password or passphrase is asked for on the
terminal as usual. One connection is shared for a couple of minutes
(ControlMaster), so a command that runs several steps asks once.
Host keys are checked as `ssh` always checks them.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path


def control_directory() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    path = Path(base) / "strmqtctl"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def run(argv: list[str], *, input: bytes | None, capture: bool, tty: bool) -> subprocess.CompletedProcess:
    # Only what is meant for it reaches the command's standard input: a
    # terminal when it is interactive, nothing otherwise. ssh asks for a
    # password on the terminal itself, whatever its input is.
    stdin = None if tty or input is not None else subprocess.DEVNULL
    return subprocess.run(
        argv,
        input=input,
        stdin=stdin,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


class Local:
    """This computer."""

    def argv(self, command: list[str], *, tty: bool = False) -> list[str]:
        return list(command)

    def run(
        self,
        command: list[str],
        *,
        input: bytes | None = None,
        capture: bool = False,
        tty: bool = False,
    ) -> subprocess.CompletedProcess:
        return run(self.argv(command, tty=tty), input=input, capture=capture, tty=tty)


class Ssh:
    """A host reached with `ssh destination`."""

    def __init__(self, destination: str, port: int | None = None, identity: str | None = None):
        self.destination = destination
        self.port = port
        self.identity = identity

    def options(self) -> list[str]:
        options = [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={control_directory()}/%C",
            "-o", "ControlPersist=120",
        ]
        if self.port:
            options += ["-p", str(self.port)]
        if self.identity:
            options += ["-i", os.path.expanduser(self.identity)]
        return options

    def argv(self, command: list[str], *, tty: bool = False) -> list[str]:
        # The remote side runs the line in the user's shell: every word is
        # quoted, so nothing in it is expanded there.
        return ["ssh", *self.options(), "-t" if tty else "-T", self.destination, "--", shlex.join(command)]

    def run(
        self,
        command: list[str],
        *,
        input: bytes | None = None,
        capture: bool = False,
        tty: bool = False,
    ) -> subprocess.CompletedProcess:
        return run(self.argv(command, tty=tty), input=input, capture=capture, tty=tty)

    def interactive(self) -> list[str]:
        """A login shell on the host."""
        return ["ssh", *self.options(), "-t", self.destination]
