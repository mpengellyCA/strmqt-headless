"""strmqtctl's commands, against this computer as the target and fake
`strmqt` and `docker` programs that say what they were asked."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import SOON, ScratchHome, firefox_profile  # noqa: E402
from strmqtctl import cli  # noqa: E402
from strmqtctl import config as configuration  # noqa: E402

RUNNING = '{"running": true}'


class CliTest(ScratchHome):
    def strmqtctl(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_targets_are_added_listed_and_forgotten(self):
        code, out, _ = self.strmqtctl("target", "add", "here", "--method", "baremetal")
        self.assertEqual(code, 0)
        self.assertIn("(the default)", out)
        code, _, _ = self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", "~/hosts/den")
        self.assertEqual(code, 0)
        # ~ is the target's home, made absolute there once.
        self.assertEqual(configuration.load().targets["box"].directory, f"{self.home}/hosts/den")
        _, out, _ = self.strmqtctl("target", "list")
        self.assertRegex(out, r"\* here\s+baremetal\s+this computer")
        self.assertRegex(out, r"  box\s+docker")
        self.assertEqual(self.strmqtctl("target", "default", "box")[0], 0)
        self.assertEqual(configuration.load().default, "box")
        self.assertEqual(self.strmqtctl("target", "remove", "box")[0], 0)
        self.assertIsNone(configuration.load().default)
        code, _, err = self.strmqtctl("target", "add", "bad name", "--method", "baremetal")
        self.assertEqual(code, 1)
        self.assertIn("Not a usable target name", err)

    def test_no_target_says_how_to_add_one(self):
        code, _, err = self.strmqtctl("status")
        self.assertEqual(code, 1)
        self.assertIn("strmqtctl target add", err)

    def test_bare_metal_commands_are_strmqt_admin(self):
        self.fake("strmqt", "exit 0\n")
        self.strmqtctl("target", "add", "here", "--method", "baremetal")
        self.assertEqual(self.strmqtctl("pair", "4831")[0], 0)
        self.assertEqual(self.strmqtctl("status", "--json")[0], 0)
        self.assertEqual(self.strmqtctl("stream", "--fps", "60", "--bitrate", "auto")[0], 0)
        self.assertEqual(self.strmqtctl("unpair", "--all")[0], 0)
        self.assertEqual(self.strmqtctl("devices")[0], 0)
        self.assertEqual(self.calls("strmqt"), [
            "admin pair 4831",
            "admin status --json",
            "admin stream --fps 60 --bitrate auto",
            "admin unpair --all",
            "admin devices",
        ])

    def test_docker_commands_are_strmqt_admin_in_the_container(self):
        self.fake("docker", "exit 0\n")
        self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", "/srv/strmqt")
        self.assertEqual(self.strmqtctl("pair", "4831")[0], 0)
        self.assertEqual(self.strmqtctl("restart")[0], 0)
        self.assertEqual(self.strmqtctl("logs", "-n", "20")[0], 0)
        self.assertEqual(self.calls("docker"), [
            "compose --project-directory /srv/strmqt exec -T strmqt strmqt admin pair 4831",
            "compose --project-directory /srv/strmqt restart strmqt",
            "compose --project-directory /srv/strmqt logs --tail 20 strmqt",
        ])

    def test_a_new_screen_size_restarts_a_containers_host(self):
        self.fake("docker", "exit 0\n")
        self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", "/srv/strmqt")
        self.assertEqual(self.strmqtctl("stream", "--height", "1080")[0], 0)
        self.assertEqual(self.calls("docker")[-1], "compose --project-directory /srv/strmqt restart strmqt")
        self.strmqtctl("stream", "--fps", "60")
        self.assertIn("admin stream --fps 60", self.calls("docker")[-1])

    def test_a_stopped_container_is_said_to_be_stopped(self):
        # exec fails, and ps lists nothing running.
        self.fake("docker", '[ "$4" = exec ] && exit 1\nexit 0\n')
        self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", "/srv/strmqt")
        code, _, err = self.strmqtctl("status")
        self.assertEqual(code, 1)
        self.assertIn("The container is not running", err)

    def test_docker_install_writes_compose_and_the_machines_groups(self):
        # `stat` and `getent` answer as a machine whose render node is 989's.
        self.fake("docker", f"case \"$*\" in *'status --json'*) echo '{RUNNING}';; esac\nexit 0\n")
        self.fake("stat", "echo 989\n")
        self.fake("getent", "echo video:x:985:\n")
        self.fake("systemctl", "echo inactive\n")
        directory = self.home / "srv"
        self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", str(directory))
        code, out, err = self.strmqtctl("install", "--tag", "0.1.0")
        self.assertEqual(code, 0, err)
        self.assertIn("is up", out)
        self.assertEqual((directory / "compose.yaml").read_text(), cli.compose_file())
        env = (directory / ".env").read_text()
        self.assertIn("STRMQT_HEADLESS_TAG=0.1.0", env)
        self.assertIn("RENDER_GID=989", env)
        self.assertIn("VIDEO_GID=985", env)
        calls = self.calls("docker")
        self.assertIn(f"compose --project-directory {directory} pull", calls)
        self.assertIn(f"compose --project-directory {directory} up -d", calls)

    def test_docker_install_will_not_take_a_running_hosts_ports(self):
        self.fake("docker", "exit 0\n")
        self.fake("systemctl", "echo active\n")
        self.strmqtctl("target", "add", "box", "--method", "docker", "--directory", "/srv/strmqt")
        code, _, err = self.strmqtctl("install")
        self.assertEqual(code, 1)
        self.assertIn("already runs a StrmQt streaming host", err)

    def test_bare_metal_install_says_what_is_missing(self):
        # Only strmqt is there; the rest are looked for on a PATH without them.
        self.fake("strmqt", "exit 0\n")
        self.fake("sh", 'PATH="' + str(self.bin) + '"; exec /bin/sh "$@"\n')
        self.strmqtctl("target", "add", "here", "--method", "baremetal")
        code, _, err = self.strmqtctl("install")
        self.assertEqual(code, 1)
        self.assertIn("missing: sunshine, kwin_wayland, pipewire, wireplumber", err)

    def test_bare_metal_install_sets_the_host_up(self):
        for program in ("sunshine", "kwin_wayland", "pipewire", "wireplumber"):
            self.fake(program, "exit 0\n")
        self.fake("strmqt", f"[ \"$2\" = status ] && echo '{RUNNING}'\nexit 0\n")
        self.strmqtctl("target", "add", "here", "--method", "baremetal")
        code, out, err = self.strmqtctl("install")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls("strmqt")[:2], ["admin help", "admin host setup"])

    def test_a_youtube_music_sign_in_is_read_here_and_sent(self):
        firefox_profile(self.home, [
            ("SID", "s", ".youtube.com", SOON, ""),
            ("BANK", "b", ".mybank.example", SOON, ""),
        ])
        self.fake("strmqt", "exit 0\n")
        self.strmqtctl("target", "add", "here", "--method", "baremetal")
        code, out, err = self.strmqtctl("youtube-music", "import")
        self.assertEqual(code, 0, err)
        self.assertIn("Sending 1 youtube.com and google.com cookies from Firefox · default-release", out)
        self.assertEqual(self.calls("strmqt"), ["admin youtube-music import -"])
        sent = json.loads(self.sent("strmqt"))
        self.assertEqual(sent["label"], "Firefox · default-release")
        self.assertEqual([c["name"] for c in sent["cookies"]], ["SID"])

        _, out, _ = self.strmqtctl("youtube-music", "import", "--list")
        self.assertIn("1. Firefox · default-release", out)
        self.assertEqual(self.strmqtctl("youtube-music", "sign-out")[0], 0)
        self.assertEqual(self.calls("strmqt")[-1], "admin youtube-music sign-out")

    def test_no_firefox_sign_in_sends_nothing(self):
        self.fake("strmqt", "exit 0\n")
        self.strmqtctl("target", "add", "here", "--method", "baremetal")
        code, _, err = self.strmqtctl("youtube-music", "import")
        self.assertEqual(code, 1)
        self.assertIn("No Firefox profile", err)
        self.assertEqual(self.calls("strmqt"), [])


if __name__ == "__main__":
    unittest.main()
