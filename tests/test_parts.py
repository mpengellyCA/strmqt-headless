"""The parts: targets, how commands reach them, what each method runs, Firefox."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import SOON, ScratchHome, firefox_profile  # noqa: E402
from strmqtctl import config as configuration  # noqa: E402
from strmqtctl import firefox  # noqa: E402
from strmqtctl.methods import BareMetal, Docker, compose_file, env_file, install_hints  # noqa: E402
from strmqtctl.transport import Ssh  # noqa: E402


class ConfigTest(ScratchHome):
    def test_targets_are_kept_for_this_user_alone(self):
        config = configuration.Config()
        config.targets["den"] = configuration.Target("den", "docker", ssh="me@mini", port=2222,
                                                     directory="/home/me/strmqt-headless")
        config.targets["here"] = configuration.Target("here", "baremetal")
        config.default = "den"
        configuration.save(config)
        path = configuration.config_path()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        loaded = configuration.load()
        self.assertEqual(loaded.default, "den")
        self.assertEqual(loaded.targets["den"].port, 2222)
        self.assertIsNone(loaded.targets["here"].ssh)
        self.assertEqual(loaded.target(None).name, "den")
        self.assertEqual(loaded.target("here").method, "baremetal")
        with self.assertRaises(configuration.ConfigError):
            loaded.target("nowhere")

    def test_an_entry_that_makes_no_sense_is_skipped(self):
        path = configuration.config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"default": "bad", "targets": {
            "bad": {"method": "kubernetes"},
            "ok": {"method": "baremetal"},
            "evil": {"method": "baremetal", "ssh": "-oProxyCommand=x"},
        }}))
        loaded = configuration.load()
        self.assertEqual(list(loaded.targets), ["ok"])
        self.assertIsNone(loaded.default)
        # One target is the target.
        self.assertEqual(loaded.target(None).name, "ok")

    def test_names_and_destinations_are_checked(self):
        for target in (
            configuration.Target("a b", "baremetal"),
            configuration.Target("ok", "docker"),  # no folder
            configuration.Target("ok", "baremetal", ssh="-oProxyCommand=evil"),
            configuration.Target("ok", "baremetal", ssh="me@host", port=70000),
        ):
            with self.assertRaises(configuration.ConfigError):
                target.validate()


class TransportTest(ScratchHome):
    def test_every_word_reaches_the_host_as_it_was(self):
        ssh = Ssh("me@mini", port=2222, identity="~/.ssh/id_strmqt")
        argv = ssh.argv(["strmqt", "admin", "pair", "12 34; rm -rf ~"])
        self.assertEqual(argv[0], "ssh")
        self.assertIn("ControlMaster=auto", argv)
        self.assertEqual(argv[argv.index("-p") + 1], "2222")
        self.assertEqual(argv[argv.index("-i") + 1], str(self.home / ".ssh/id_strmqt"))
        self.assertEqual(argv[-3:-1], ["me@mini", "--"])
        self.assertEqual(shlex.split(argv[-1]), ["strmqt", "admin", "pair", "12 34; rm -rf ~"])
        self.assertIn("-T", argv)
        self.assertIn("-t", ssh.argv(["true"], tty=True))
        # Host keys are ssh's business, checked as always.
        self.assertFalse(any("StrictHostKeyChecking" in word for word in argv))


class MethodsTest(unittest.TestCase):
    def test_docker_runs_admin_inside_the_container(self):
        docker = Docker("/home/me/strmqt-headless")
        self.assertEqual(
            docker.admin(["pair", "1234"]),
            ["docker", "compose", "--project-directory", "/home/me/strmqt-headless",
             "exec", "-T", "strmqt", "strmqt", "admin", "pair", "1234"],
        )
        self.assertEqual(docker.logs(True, 50)[-4:], ["--follow", "--tail", "50", "strmqt"])

    def test_bare_metal_runs_admin_as_it_is(self):
        metal = BareMetal()
        self.assertEqual(metal.admin(["status", "--json"]), ["strmqt", "admin", "status", "--json"])
        self.assertEqual(metal.restart(), ["strmqt", "admin", "host", "restart"])
        self.assertIn("strmqt-host-sunshine.service", metal.logs(False, None))

    def test_the_compose_file_is_the_one_in_the_repository(self):
        root = Path(__file__).resolve().parent.parent / "compose.yaml"
        self.assertEqual(root.read_text(), compose_file())
        self.assertIn("network_mode: host", compose_file())
        self.assertIn("${RENDER_GID", compose_file())

    def test_the_env_file_names_the_machines_groups(self):
        text = env_file("0.1.0", "989", "985")
        self.assertIn("STRMQT_HEADLESS_TAG=0.1.0\n", text)
        self.assertIn("RENDER_GID=989\n", text)
        self.assertNotIn("VIDEO_GID", env_file("latest", "989", None))

    def test_install_hints_are_in_the_distributions_words(self):
        arch = install_hints('ID=cachyos\nID_LIKE="arch"\n', ["kwin_wayland", "sunshine"])
        self.assertEqual(arch[0], "sudo pacman -S kwin")
        self.assertIn("Sunshine", arch[1])
        ubuntu = install_hints("ID=ubuntu\nID_LIKE=debian\n", ["pipewire", "wireplumber", "strmqt"])
        self.assertEqual(ubuntu[0], "sudo apt install pipewire wireplumber")
        self.assertIn("StrmQt 0.9.2", ubuntu[1])
        self.assertEqual(install_hints("ID=fedora\n", ["kwin_wayland"]), ["sudo dnf install kwin-wayland"])


class FirefoxTest(ScratchHome):
    def test_each_container_is_its_own_source_signed_in_first(self):
        profile = firefox_profile(self.home, [
            ("PREF", "p", ".youtube.com", SOON, ""),
            ("SID", "work", ".youtube.com", SOON, "^userContextId=2"),
            ("SID", "framed", ".youtube.com", SOON, "^partitionKey=%28https%2Cexample.com%29"),
            ("SID", "elsewhere", ".example.com", SOON, "^userContextId=3"),
        ], containers='{"identities":[{"userContextId":2,"l10nId":"user-context-work"}]}')
        found = firefox.sources(firefox.profile_directories(firefox.default_roots()))
        self.assertEqual([s.label for s in found],
                         ["Firefox · default-release · Work", "Firefox · default-release"])
        self.assertTrue(found[0].signed_in)
        self.assertFalse(found[1].signed_in)
        self.assertEqual(found[0].database, profile / "cookies.sqlite")

    def test_only_youtube_and_google_leave_this_computer(self):
        firefox_profile(self.home, [
            ("SID", "s", ".youtube.com", SOON, ""),
            ("HSID", "h", "accounts.google.com", SOON * 1000, ""),
            ("OLD", "o", ".google.com", 1000, ""),
            ("BANK", "b", ".mybank.example", SOON, ""),
            ("LOOK", "l", ".notgoogle.com", SOON, ""),
            ("SID", "other", ".youtube.com", SOON, "^userContextId=5"),
        ])
        source = firefox.sources(firefox.profile_directories(firefox.default_roots()))[0]
        carried = firefox.sign_in(source)
        self.assertEqual(carried["label"], "Firefox · default-release")
        self.assertEqual(sorted(c["name"] for c in carried["cookies"]), ["HSID", "SID"])
        self.assertEqual({c["value"] for c in carried["cookies"]}, {"s", "h"})
        # Milliseconds are seconds once sent.
        self.assertEqual(max(c["expiry"] for c in carried["cookies"]), SOON)

    def test_a_store_firefox_has_open_is_read_from_a_copy(self):
        profile = firefox_profile(self.home, [("SID", "s", ".youtube.com", SOON, "")])
        store = profile / "cookies.sqlite"
        before = store.read_bytes()
        os.chmod(store, 0o400)  # nothing here may write to it
        firefox.sign_in(firefox.sources([profile])[0])
        self.assertEqual(store.read_bytes(), before)
        self.assertFalse(Path(str(store) + "-journal").exists())

    def test_no_firefox_is_no_sources(self):
        self.assertEqual(firefox.sources(firefox.profile_directories(firefox.default_roots())), [])


if __name__ == "__main__":
    unittest.main()
