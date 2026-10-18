"""How StrmQt is installed on a target: in Docker, or on the machine itself.

This is the only thing that differs between the two. Either way the host is
administered by `strmqt admin` on the target (StrmQt's src/app/admin/Admin.h):
in Docker it runs inside the container, through `docker compose exec`; on
bare metal it runs as it is, as the user the host belongs to.
"""

from __future__ import annotations

import shlex
from importlib import resources

STRMQT_RELEASES = "https://github.com/mpengellyCA/strmQt-linux/releases"
SUNSHINE_RELEASES = "https://github.com/LizardByte/Sunshine/releases"
UNITS = ("strmqt-host.service", "strmqt-host-sunshine.service", "strmqt-host-audio.service")
# What a bare-metal host needs on its PATH.
PROGRAMS = ("strmqt", "sunshine", "kwin_wayland", "pipewire", "wireplumber")
SERVICE = "strmqt"


def compose_file() -> str:
    return resources.files("strmqtctl").joinpath("compose.yaml").read_text(encoding="utf-8")


def env_file(tag: str, render_gid: str | None, video_gid: str | None) -> str:
    lines = [
        "# Written by strmqtctl install; read by compose.yaml.",
        f"STRMQT_HEADLESS_TAG={tag}",
    ]
    if render_gid:
        lines.append(f"RENDER_GID={render_gid}")
    if video_gid:
        lines.append(f"VIDEO_GID={video_gid}")
    return "\n".join(lines) + "\n"


def write_file_command(directory: str, name: str) -> list[str]:
    """A command that writes its standard input to directory/name."""
    return ["sh", "-c", 'mkdir -p "$1" && cat > "$1/$2"', "sh", directory, name]


class Docker:
    name = "docker"

    def __init__(self, directory: str):
        self.directory = directory

    def compose(self, *arguments: str) -> list[str]:
        return ["docker", "compose", "--project-directory", self.directory, *arguments]

    def admin(self, arguments: list[str]) -> list[str]:
        # -T: no terminal, so a sign-in can be piped in.
        return self.compose("exec", "-T", SERVICE, "strmqt", "admin", *arguments)

    def start(self) -> list[str]:
        return self.compose("up", "-d")

    def stop(self) -> list[str]:
        return self.compose("stop")

    def restart(self) -> list[str]:
        return self.compose("restart", SERVICE)

    def logs(self, follow: bool, lines: int | None) -> list[str]:
        command = self.compose("logs")
        if follow:
            command.append("--follow")
        if lines:
            command += ["--tail", str(lines)]
        return command + [SERVICE]

    def shell(self) -> list[str]:
        return self.compose("exec", SERVICE, "bash")

    def running(self) -> list[str]:
        return self.compose("ps", "--status", "running", "--quiet", SERVICE)


class BareMetal:
    name = "baremetal"

    def admin(self, arguments: list[str]) -> list[str]:
        return ["strmqt", "admin", *arguments]

    def start(self) -> list[str]:
        return ["systemctl", "--user", "start", UNITS[0], UNITS[1]]

    def stop(self) -> list[str]:
        return ["systemctl", "--user", "stop", UNITS[1], UNITS[0], UNITS[2]]

    def restart(self) -> list[str]:
        return self.admin(["host", "restart"])

    def logs(self, follow: bool, lines: int | None) -> list[str]:
        command = ["journalctl", "--user", "--no-pager"]
        for unit in UNITS:
            command += ["-u", unit]
        if follow:
            command.append("--follow")
        if lines:
            command += ["-n", str(lines)]
        return command

    def missing_programs(self) -> list[str]:
        """Prints each of PROGRAMS that is not on the target's PATH."""
        check = "for p in " + " ".join(shlex.quote(p) for p in PROGRAMS) + "; do command -v \"$p\" >/dev/null || echo \"$p\"; done"
        return ["sh", "-c", check]


def install_hints(os_release: str, missing: list[str]) -> list[str]:
    """What to install, in the words of the target's distribution."""
    fields = {}
    for line in os_release.splitlines():
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip().strip('"')
    family = " ".join([fields.get("ID", ""), fields.get("ID_LIKE", "")]).split()
    system = [p for p in missing if p in ("kwin_wayland", "pipewire", "wireplumber")]
    hints = []
    if system:
        if "arch" in family:
            names = {"kwin_wayland": "kwin", "pipewire": "pipewire", "wireplumber": "wireplumber"}
            hints.append("sudo pacman -S " + " ".join(names[p] for p in system))
        elif "fedora" in family:
            names = {"kwin_wayland": "kwin-wayland", "pipewire": "pipewire", "wireplumber": "wireplumber"}
            hints.append("sudo dnf install " + " ".join(names[p] for p in system))
        elif "debian" in family or "ubuntu" in family:
            # KWin's screencast, which Sunshine captures, is in kwin-common.
            names = {"kwin_wayland": "kwin-wayland kwin-common", "pipewire": "pipewire", "wireplumber": "wireplumber"}
            hints.append("sudo apt install " + " ".join(names[p] for p in system))
        else:
            hints.append("Install your distribution's packages for: " + ", ".join(system))
    if "strmqt" in missing:
        hints.append(f"StrmQt 0.9.2 or newer, your distribution's package from {STRMQT_RELEASES}")
    if "sunshine" in missing:
        hints.append(f"Sunshine (a 2026 release), your distribution's package from {SUNSHINE_RELEASES}")
    return hints


def method_for(target) -> Docker | BareMetal:
    if target.method == "docker":
        return Docker(target.directory)
    return BareMetal()
