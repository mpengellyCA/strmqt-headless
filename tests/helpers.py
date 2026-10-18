"""Scratch homes, fake programs and fake Firefox profiles for the tests."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

SOON = 4_000_000_000  # 2096


def write_store(path: Path, rows: list[tuple]) -> None:
    """rows: (name, value, host, expiry, originAttributes)."""
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL DEFAULT '',"
        " name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER,"
        " creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, sameSite INTEGER)"
    )
    for name, value, host, expiry, attributes in rows:
        db.execute(
            "INSERT INTO moz_cookies (originAttributes, name, value, host, path, expiry, lastAccessed,"
            " isSecure, isHttpOnly, sameSite) VALUES (?, ?, ?, ?, '/', ?, 1790000000000000, 1, 1, 0)",
            (attributes, name, value, host, expiry),
        )
    db.commit()
    db.close()


def firefox_profile(home: Path, rows: list[tuple], containers: str | None = None) -> Path:
    root = home / ".mozilla/firefox"
    profile = root / "abcd.default-release"
    profile.mkdir(parents=True)
    (root / "profiles.ini").write_text(
        "[Install1]\nDefault=abcd.default-release\n\n"
        "[Profile0]\nName=default-release\nIsRelative=1\nPath=abcd.default-release\n"
    )
    write_store(profile / "cookies.sqlite", rows)
    if containers:
        (profile / "containers.json").write_text(containers)
    return profile


class ScratchHome(unittest.TestCase):
    """A HOME, XDG_CONFIG_HOME and PATH of the test's own."""

    def setUp(self) -> None:
        self.scratch = tempfile.TemporaryDirectory()
        self.home = Path(self.scratch.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.saved = {key: os.environ.get(key) for key in ("HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "PATH")}
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        os.environ["XDG_RUNTIME_DIR"] = str(self.home / "run")
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{self.saved['PATH']}"

    def tearDown(self) -> None:
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.scratch.cleanup()

    def fake(self, name: str, script: str) -> Path:
        """A program on PATH; it logs its arguments and standard input to <name>.log."""
        log = self.home / f"{name}.log"
        path = self.bin / name
        path.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{log}"\n'
            f'if [ ! -t 0 ]; then cat >> "{log}.in"; fi\n'
            + script
        )
        path.chmod(0o755)
        return log

    def calls(self, name: str) -> list[str]:
        log = self.home / f"{name}.log"
        return log.read_text().splitlines() if log.exists() else []

    def sent(self, name: str) -> str:
        log = self.home / f"{name}.log.in"
        return log.read_text() if log.exists() else ""
