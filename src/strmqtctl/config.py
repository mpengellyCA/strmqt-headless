"""Where strmqtctl keeps its targets: the hosts it administers, and how.

A target is a machine (this one, or one reached over SSH) and an
installation method (Docker, or bare metal). Nothing secret is kept here:
SSH keeps its own keys and known hosts, and a host's pairings and sign-ins
stay on the host.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

METHODS = ("docker", "baremetal")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


class ConfigError(Exception):
    pass


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "strmqtctl" / "config.json"


@dataclass
class Target:
    name: str
    method: str
    # "user@host" or "host"; None is this computer.
    ssh: str | None = None
    port: int | None = None
    identity: str | None = None
    # Docker: the folder on the target that holds compose.yaml and .env.
    directory: str | None = None

    def validate(self) -> None:
        if not NAME.match(self.name):
            raise ConfigError(f"Not a usable target name: {self.name!r} (letters, digits, '.', '_', '-').")
        if self.method not in METHODS:
            raise ConfigError(f"The method is docker or baremetal, not {self.method!r}.")
        if self.ssh is not None and (not self.ssh or self.ssh.startswith("-") or any(c.isspace() for c in self.ssh)):
            raise ConfigError(f"Not an SSH destination: {self.ssh!r}.")
        if self.port is not None and not 0 < self.port < 65536:
            raise ConfigError(f"Port {self.port} is out of range.")
        if self.method == "docker" and not self.directory:
            raise ConfigError("A Docker target needs the folder its compose.yaml is in.")

    @property
    def where(self) -> str:
        return self.ssh if self.ssh else "this computer"


@dataclass
class Config:
    default: str | None = None
    targets: dict[str, Target] = field(default_factory=dict)

    def target(self, name: str | None) -> Target:
        chosen = name or self.default
        if not chosen:
            if len(self.targets) == 1:
                return next(iter(self.targets.values()))
            raise ConfigError(
                "No target chosen. Add one with `strmqtctl target add`, or name one with --target."
                if not self.targets
                else "Several targets and no default: name one with --target, "
                "or choose a default with `strmqtctl target default NAME`."
            )
        try:
            return self.targets[chosen]
        except KeyError:
            raise ConfigError(f"There is no target named {chosen!r}.") from None


def load(path: Path | None = None) -> Config:
    path = path or config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as error:
        raise ConfigError(f"{path} cannot be read: {error}") from None
    config = Config(default=raw.get("default"))
    for name, entry in (raw.get("targets") or {}).items():
        known = {key: entry.get(key) for key in ("method", "ssh", "port", "identity", "directory")}
        target = Target(name=name, **known)
        try:
            target.validate()
        except ConfigError:
            continue  # a hand-edited entry that no longer makes sense is skipped
        config.targets[name] = target
    if config.default not in config.targets:
        config.default = None
    return config


def save(config: Config, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "default": config.default,
        "targets": {
            name: {key: value for key, value in asdict(target).items() if key != "name" and value is not None}
            for name, target in sorted(config.targets.items())
        },
    }
    temporary = path.with_suffix(".tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")
    os.replace(temporary, path)
