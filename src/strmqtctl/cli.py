"""strmqtctl — administers StrmQt streaming hosts that nobody sits at.

    strmqtctl target add den --ssh me@mini-pc --method docker
    strmqtctl install
    strmqtctl pair 1234
    strmqtctl youtube-music import

A target is a machine and an installation method. Every other command is the
same for every target: it runs `strmqt admin` there, over SSH or here,
inside Docker or not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

from . import __version__, config as configuration, firefox
from .methods import (
    ADMIN_STRMQT,
    BareMetal,
    Docker,
    compose_file,
    env_file,
    install_hints,
    method_for,
    write_file_command,
)
from .transport import Local, Ssh

DEFAULT_DIRECTORY = "~/strmqt-headless"


class Failure(Exception):
    """Something the user is told, then strmqtctl exits non-zero."""


def say(*parts: object) -> None:
    print(*parts, flush=True)


def warn(*parts: object) -> None:
    print(*parts, file=sys.stderr, flush=True)


def transport_for(target):
    if target.ssh:
        return Ssh(target.ssh, target.port, target.identity)
    return Local()


def text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout or b"").decode("utf-8", "replace").strip()


class Session:
    """One target, and how to reach it."""

    def __init__(self, target):
        self.target = target
        self.transport = transport_for(target)
        self.method = method_for(target)

    def run(self, command, **options) -> int:
        return self.transport.run(command, **options).returncode

    def output(self, command) -> tuple[int, str]:
        result = self.transport.run(command, capture=True)
        return result.returncode, text(result)

    def admin(self, arguments, *, input: bytes | None = None) -> int:
        code = self.run(self.method.admin(arguments), input=input)
        if code != 0 and isinstance(self.method, Docker):
            running, ids = self.output(self.method.running())
            if running == 0 and not ids:
                warn(f"The container is not running on {self.target.where}: `strmqtctl start` starts it.")
        elif code == 127:
            warn(f"StrmQt is not installed on {self.target.where}.")
        return code


# ── targets ───────────────────────────────────────────────────────────────


def resolve_directory(transport, directory: str) -> str:
    """`~/x` on the target, as an absolute path there."""
    if not directory.startswith("~"):
        return directory
    result = transport.run(["sh", "-c", 'printf %s "$HOME"'], capture=True)
    home = text(result)
    if result.returncode != 0 or not home.startswith("/"):
        raise Failure("Could not find the home folder on the target.")
    return home + directory[1:]


def target_add(args, config) -> int:
    target = configuration.Target(
        name=args.name,
        method=args.method,
        ssh=args.ssh,
        port=args.port,
        identity=args.identity,
        directory=args.directory if args.method == "docker" else None,
    )
    if target.method == "docker" and not target.directory:
        target.directory = DEFAULT_DIRECTORY
    target.validate()
    transport = transport_for(target)
    if target.ssh:
        if args.copy_id:
            command = ["ssh-copy-id"]
            if target.port:
                command += ["-p", str(target.port)]
            if target.identity:
                command += ["-i", target.identity]
            if subprocess.run(command + [target.ssh]).returncode != 0:
                raise Failure("ssh-copy-id did not install the key.")
        say(f"Connecting to {target.ssh}…")
        result = transport.run(["true"])
        if result.returncode != 0:
            raise Failure(f"Could not connect to {target.ssh} with ssh.")
    if target.directory:
        target.directory = resolve_directory(transport, target.directory)
    config.targets[target.name] = target
    if args.default or config.default is None:
        config.default = target.name
    configuration.save(config)
    say(f"Target {target.name}: {target.method} on {target.where}"
        + (f", in {target.directory}" if target.directory else "")
        + (" (the default)" if config.default == target.name else ""))
    say("Next: strmqtctl install" + ("" if config.default == target.name else f" --target {target.name}"))
    return 0


def target_list(args, config) -> int:
    if not config.targets:
        say("No targets yet. Add one with `strmqtctl target add NAME --method docker|baremetal [--ssh USER@HOST]`.")
        return 0
    for name, target in sorted(config.targets.items()):
        marker = "*" if name == config.default else " "
        where = target.where + (f":{target.port}" if target.port else "")
        extra = f"  {target.directory}" if target.directory else ""
        say(f"{marker} {name:<16} {target.method:<10} {where}{extra}")
    return 0


def target_remove(args, config) -> int:
    config.target(args.name)
    del config.targets[args.name]
    if config.default == args.name:
        config.default = None
    configuration.save(config)
    say(f"Target {args.name} is forgotten here. Nothing on it was changed.")
    return 0


def target_default(args, config) -> int:
    config.target(args.name)
    config.default = args.name
    configuration.save(config)
    say(f"{args.name} is the default target.")
    return 0


# ── installing ────────────────────────────────────────────────────────────


def install_docker(session: Session, args) -> int:
    method: Docker = session.method
    where = session.target.where
    code, _ = session.output(["docker", "compose", "version"])
    if code != 0:
        raise Failure(f"Docker with its compose plugin is needed on {where} (`docker compose version` failed). "
                      "If Docker is there, the user may need to be in the docker group.")
    _, host_state = session.output(["sh", "-c", "systemctl --user is-active strmqt-host.service 2>/dev/null || true"])
    if host_state.strip() == "active" and not args.force:
        raise Failure(f"{where} already runs a StrmQt streaming host outside Docker, on the same ports. "
                      "Remove it there first (Settings → Streaming host → Remove), or pass --force.")
    _, gids = session.output(["sh", "-c",
                              "stat -c %g /dev/dri/renderD* 2>/dev/null | head -n 1; "
                              "getent group video | cut -d: -f3"])
    lines = gids.splitlines()
    render = lines[0].strip() if lines and lines[0].strip().isdigit() else None
    video = lines[1].strip() if len(lines) > 1 and lines[1].strip().isdigit() else None
    if render is None:
        warn(f"No GPU render node was found on {where} (/dev/dri/renderD*): the stream would have no hardware encoder.")
        raise Failure("A streaming host needs a GPU. Pass it through, or install on a machine that has one.")
    for name, content in (("compose.yaml", compose_file()), (".env", env_file(args.tag, render, video))):
        if session.run(write_file_command(method.directory, name), input=content.encode()) != 0:
            raise Failure(f"Could not write {method.directory}/{name} on {where}.")
    say(f"Wrote compose.yaml and .env in {method.directory} on {where}.")
    if not args.no_pull and session.run(method.compose("pull")) != 0:
        raise Failure("The image could not be pulled.")
    if session.run(method.start()) != 0:
        raise Failure("The container did not start.")
    return wait_until_up(session)


def install_baremetal(session: Session, args) -> int:
    method: BareMetal = session.method
    where = session.target.where
    _, missing = session.output(method.missing_programs())
    missing = missing.split()
    if missing:
        _, os_release = session.output(["cat", "/etc/os-release"])
        warn(f"{where} is missing: {', '.join(missing)}")
        for hint in install_hints(os_release, missing):
            warn(f"  {hint}")
        raise Failure("Install those, then run `strmqtctl install` again.")
    code, _ = session.output(method.admin(["help"]))
    if code != 0:
        raise Failure(f"The StrmQt on {where} has no `strmqt admin`: {ADMIN_STRMQT}.")
    if session.admin(["host", "setup"]) != 0:
        raise Failure("The streaming host was not set up.")
    return wait_until_up(session)


def wait_until_up(session: Session, seconds: int = 60) -> int:
    say("Waiting for the host's StrmQt…")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        code, output = session.output(session.method.admin(["status", "--json"]))
        if code == 0:
            try:
                if json.loads(output).get("running"):
                    say(f"The streaming host on {session.target.where} is up.")
                    say("Next: open StrmLight on the TV and run `strmqtctl pair CODE` with the code it shows.")
                    return 0
            except ValueError:
                pass
        time.sleep(3)
    warn("The host did not come up in a minute. `strmqtctl logs` says why.")
    return 1


def install(session: Session, args) -> int:
    if isinstance(session.method, Docker):
        return install_docker(session, args)
    return install_baremetal(session, args)


def uninstall(session: Session, args) -> int:
    if isinstance(session.method, Docker):
        command = session.method.compose("down")
        if args.purge:
            if not args.yes and not confirm(
                "Also delete the host's volume: its pairings, Emby and YouTube sign-ins?"):
                return 1
            command.append("--volumes")
        return session.run(command)
    if args.purge:
        warn("Bare metal: the services are removed; the host's folder and pairings are kept.")
    return session.admin(["host", "remove"])


def update(session: Session, args) -> int:
    if isinstance(session.method, Docker):
        if session.run(session.method.compose("pull")) != 0:
            return 1
        return session.run(session.method.start())
    warn(f"On bare metal StrmQt is updated by {session.target.where}'s package manager;"
         " the host is restarted now so it runs what is installed.")
    return session.admin(["host", "restart"])


def confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


# ── YouTube Music ─────────────────────────────────────────────────────────


def local_sources() -> list[firefox.Source]:
    found = firefox.sources(firefox.profile_directories(firefox.default_roots()))
    if not found:
        raise Failure("No Firefox profile with a YouTube or Google sign-in was found on this computer.")
    return found


def list_sources(found: list[firefox.Source]) -> None:
    for index, source in enumerate(found, 1):
        say(f"{index}. {source.label}" + ("" if source.signed_in else "  (not signed in)"))


def choose_source(args, found: list[firefox.Source]) -> firefox.Source:
    if args.source:
        matches = [s for s in found if args.source.lower() in s.label.lower()]
        if args.source.isdigit() and 0 < int(args.source) <= len(found):
            matches = [found[int(args.source) - 1]]
        if len(matches) != 1:
            raise Failure(f"{len(matches) or 'No'} sources match {args.source!r}; "
                          "`strmqtctl youtube-music import --list` lists them.")
        return matches[0]
    signed_in = [s for s in found if s.signed_in]
    if len(signed_in) == 1 or not sys.stdin.isatty():
        return (signed_in or found)[0]
    list_sources(found)
    answer = input(f"Copy the sign-in from which? [1-{len(found)}, 1] ").strip() or "1"
    if not answer.isdigit() or not 0 < int(answer) <= len(found):
        raise Failure("No source chosen.")
    return found[int(answer) - 1]


def youtube_music(session: Session, args) -> int:
    if args.action == "sign-out":
        return session.admin(["youtube-music", "sign-out"])
    found = local_sources()
    if args.list:
        list_sources(found)
        return 0
    source = choose_source(args, found)
    carried = firefox.sign_in(source)
    if not carried["cookies"]:
        raise Failure(f"There is no YouTube sign-in in {source.label}.")
    say(f"Sending {len(carried['cookies'])} youtube.com and google.com cookies from {source.label} "
        f"to {session.target.where}…")
    return session.admin(["youtube-music", "import", "-"], input=json.dumps(carried).encode())


# ── the rest: the same for every target ──────────────────────────────────


def passthrough(arguments):
    def command(session: Session, args) -> int:
        extra = [a for a in getattr(args, "rest", []) if a != "--"]
        return session.admin(arguments(args) + extra)
    return command


def shell(session: Session, args) -> int:
    if isinstance(session.method, Docker):
        return session.run(session.method.shell(), tty=True)
    if isinstance(session.transport, Ssh):
        return subprocess.run(session.transport.interactive()).returncode
    warn("This computer is the target: its shell is the one you are in.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="strmqtctl",
        description="Administers StrmQt streaming hosts, in Docker or on bare metal, here or over SSH.",
    )
    parser.add_argument("--version", action="version", version=f"strmqtctl {__version__}")
    parser.add_argument("-t", "--target", help="the target to act on (default: the default target)")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    target = commands.add_parser("target", help="add, list or remove the hosts strmqtctl knows")
    targets = target.add_subparsers(dest="action", metavar="ACTION", required=True)
    add = targets.add_parser("add", help="add a target")
    add.add_argument("name")
    add.add_argument("--method", choices=configuration.METHODS, required=True,
                     help="how StrmQt is installed there")
    add.add_argument("--ssh", metavar="[USER@]HOST", help="reach it with ssh (default: this computer)")
    add.add_argument("--port", type=int, help="its SSH port")
    add.add_argument("--identity", metavar="FILE", help="the SSH key to use")
    add.add_argument("--copy-id", action="store_true", help="install your SSH key there first (ssh-copy-id)")
    add.add_argument("--directory", default=DEFAULT_DIRECTORY,
                     help=f"Docker: the folder for compose.yaml there (default: {DEFAULT_DIRECTORY})")
    add.add_argument("--default", action="store_true", help="make it the default target")
    add.set_defaults(local=target_add)
    listing = targets.add_parser("list", help="list the targets")
    listing.set_defaults(local=target_list)
    remove = targets.add_parser("remove", help="forget a target (changes nothing on it)")
    remove.add_argument("name")
    remove.set_defaults(local=target_remove)
    default = targets.add_parser("default", help="choose the default target")
    default.add_argument("name")
    default.set_defaults(local=target_default)

    setup = commands.add_parser("install", help="install and start the streaming host on the target")
    setup.add_argument("--tag", default="latest", help="Docker: the image tag (default: latest)")
    setup.add_argument("--no-pull", action="store_true", help="Docker: use the image already there")
    setup.add_argument("--force", action="store_true", help="install even where a host already runs")
    setup.set_defaults(remote=install)
    gone = commands.add_parser("uninstall", help="stop and remove the streaming host")
    gone.add_argument("--purge", action="store_true", help="Docker: also delete the host's volume")
    gone.add_argument("-y", "--yes", action="store_true", help="do not ask before --purge")
    gone.set_defaults(remote=uninstall)
    newer = commands.add_parser("update", help="Docker: pull the newest image and restart on it")
    newer.set_defaults(remote=update)
    for name, help_text, attribute in (
        ("start", "start the streaming host", "start"),
        ("stop", "stop the streaming host", "stop"),
        ("restart", "restart the streaming host", "restart"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.set_defaults(remote=lambda session, args, attribute=attribute:
                             session.run(getattr(session.method, attribute)()))
    logs = commands.add_parser("logs", help="what the host's services said")
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("-n", "--lines", type=int, default=200)
    logs.set_defaults(remote=lambda session, args:
                      session.run(session.method.logs(args.follow, args.lines), tty=args.follow))
    sh = commands.add_parser("shell", help="a shell on the target (Docker: in the container)")
    sh.set_defaults(remote=shell)

    status = commands.add_parser("status", help="what the streaming host is, and how it is")
    status.add_argument("--json", action="store_true")
    status.set_defaults(remote=passthrough(lambda a: ["status"] + (["--json"] if a.json else [])))
    pair = commands.add_parser("pair", help="pair the StrmLight showing CODE")
    pair.add_argument("code")
    pair.set_defaults(remote=passthrough(lambda a: ["pair", a.code]))
    devices = commands.add_parser("devices", help="the paired StrmLights and phones")
    devices.add_argument("--json", action="store_true")
    devices.set_defaults(remote=passthrough(lambda a: ["devices"] + (["--json"] if a.json else [])))
    unpair = commands.add_parser("unpair", help="unpair a device by its id, or --all")
    unpair.add_argument("id", nargs="?")
    unpair.add_argument("--all", action="store_true")
    unpair.set_defaults(remote=passthrough(lambda a: ["unpair"] + (["--all"] if a.all else [a.id or ""])))
    stream = commands.add_parser("stream", help="show or change the stream's settings")
    stream.add_argument("--fps", choices=("30", "60"))
    stream.add_argument("--bitrate", metavar="KBIT/S|auto")
    stream.add_argument("--codec", choices=("auto", "hevc", "av1", "h264"))
    stream.add_argument("--audio", choices=("2", "51", "71"))
    stream.add_argument("--height", choices=("720", "1080", "1440", "2160"))
    stream.add_argument("--json", action="store_true")

    def stream_arguments(a) -> list[str]:
        out = ["stream"]
        for option in ("fps", "bitrate", "codec", "audio", "height"):
            if getattr(a, option) is not None:
                out += [f"--{option}", getattr(a, option)]
        return out + (["--json"] if a.json else [])

    def stream_command(session: Session, args) -> int:
        code = session.admin(stream_arguments(args))
        # A container's host takes a new screen size when it starts again.
        if code == 0 and args.height and isinstance(session.method, Docker):
            say("Restarting the host for the new screen size…")
            code = session.run(session.method.restart())
        return code

    stream.set_defaults(remote=stream_command)

    music = commands.add_parser("youtube-music", help="sign the host's YouTube Music in or out")
    music_actions = music.add_subparsers(dest="action", metavar="ACTION", required=True)
    music_import = music_actions.add_parser(
        "import", help="copy the YouTube sign-in of a Firefox profile on this computer to the host")
    music_import.add_argument("--source", help="the profile or container: its number in --list, or part of its name")
    music_import.add_argument("--list", action="store_true", help="list the Firefox profiles and containers")
    music_import.set_defaults(remote=youtube_music)
    music_out = music_actions.add_parser("sign-out", help="sign the host's YouTube Music out")
    music_out.set_defaults(remote=youtube_music)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = configuration.load()
        if hasattr(args, "local"):
            return args.local(args, config)
        if args.command == "unpair" and not args.all and not args.id:
            parser.error("unpair takes a device's id, or --all")
        return args.remote(Session(config.target(args.target)), args)
    except (Failure, configuration.ConfigError) as problem:
        warn(f"strmqtctl: {problem}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
