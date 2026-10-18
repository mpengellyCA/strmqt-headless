"""A YouTube Music sign-in, read out of Firefox on this computer.

Google will not let StrmQt's embedded browser sign in, so StrmQt borrows the
session of a Firefox that is signed in (StrmQt's src/app/FirefoxCookies.h).
A streaming host has no Firefox and nobody at its screen, so the sign-in is
read here, on the administrator's computer, and sent to the host.

Only the cookies of youtube.com and google.com, and only those of the one
profile or container chosen, ever leave this computer. Firefox's own files
are never written: its store is copied, with its write-ahead log, and the
copy is read.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DOMAINS = ("youtube.com", "google.com")


@dataclass
class Source:
    profile_name: str
    container_name: str
    database: Path
    origin_attributes: str
    signed_in: bool
    last_used: int  # seconds since the epoch; 0 when unknown

    @property
    def label(self) -> str:
        if self.container_name:
            return f"Firefox · {self.profile_name} · {self.container_name}"
        return f"Firefox · {self.profile_name}"


def default_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".mozilla/firefox",
        home / ".config/mozilla/firefox",
        home / "snap/firefox/common/.mozilla/firefox",
        home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
        # macOS
        home / "Library/Application Support/Firefox",
    ]


def profile_directories(roots: list[Path]) -> list[Path]:
    """Profiles under `roots` that have a cookie store, the installation's default first."""
    first: list[Path] = []
    rest: list[Path] = []
    for root in roots:
        ini = root / "profiles.ini"
        if not ini.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str  # keys are case-sensitive in profiles.ini
        try:
            parser.read(ini, encoding="utf-8")
        except configparser.Error:
            continue
        defaults = {parser[s].get("Default", "") for s in parser.sections() if s.startswith("Install")}
        for section in parser.sections():
            if not section.startswith("Profile"):
                continue
            entry = parser[section]
            path = entry.get("Path", "")
            if not path:
                continue
            relative = entry.get("IsRelative", "1") != "0"
            directory = Path(os.path.normpath(root / path if relative else path))
            if not (directory / "cookies.sqlite").is_file() or directory in first or directory in rest:
                continue
            (first if path in defaults else rest).append(directory)
    return first + rest


class _Snapshot:
    """The store as it stands, copied with its log so Firefox is not disturbed."""

    def __init__(self, database: Path):
        self.directory = tempfile.TemporaryDirectory(prefix="strmqtctl-")
        copy = Path(self.directory.name) / "cookies.sqlite"
        shutil.copyfile(database, copy)
        wal = Path(str(database) + "-wal")
        if wal.is_file():
            shutil.copyfile(wal, Path(str(copy) + "-wal"))
        self.connection = sqlite3.connect(copy)

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, *exc) -> None:
        self.connection.close()
        self.directory.cleanup()


def _container_id(origin_attributes: str) -> int:
    for part in origin_attributes.lstrip("^").split("&"):
        if part.startswith("userContextId="):
            try:
                return int(part[len("userContextId="):])
            except ValueError:
                return 0
    return 0


def _container_name(profile: Path, identity: int) -> str:
    try:
        identities = json.loads((profile / "containers.json").read_text(encoding="utf-8")).get("identities", [])
    except (OSError, ValueError):
        identities = []
    for entry in identities:
        if entry.get("userContextId") != identity:
            continue
        if entry.get("name"):
            return entry["name"]
        key = (entry.get("l10nId") or entry.get("l10nID") or "").lower()
        for word in ("personal", "work", "banking", "shopping"):
            if word in key:
                return word.capitalize()
    return f"Container {identity}"


def _seconds(stamp: int) -> int:
    # Firefox wrote seconds, then milliseconds (and microseconds for lastAccessed).
    while stamp > 100_000_000_000:
        stamp //= 1000
    return stamp


def _wanted(host: str) -> bool:
    bare = host[1:] if host.startswith(".") else host
    return any(bare == domain or bare.endswith("." + domain) for domain in DOMAINS)


def sources(profiles: list[Path]) -> list[Source]:
    """Every place a sign-in can be copied from, signed-in ones first."""
    found: list[Source] = []
    for profile in profiles:
        folder = profile.name
        name = folder.split(".", 1)[1] if "." in folder else folder
        try:
            snapshot = _Snapshot(profile / "cookies.sqlite")
        except (OSError, sqlite3.Error):
            continue
        with snapshot as db:
            try:
                rows = db.execute(
                    "SELECT originAttributes,"
                    " MAX(CASE WHEN name IN ('SID', '__Secure-3PSID')"
                    "           AND (host = '.youtube.com' OR host = '.google.com') THEN 1 ELSE 0 END),"
                    " MAX(lastAccessed)"
                    " FROM moz_cookies"
                    " WHERE host = 'youtube.com' OR host LIKE '%.youtube.com'"
                    "    OR host = 'google.com' OR host LIKE '%.google.com'"
                    " GROUP BY originAttributes"
                ).fetchall()
            except sqlite3.Error:
                continue
        for attributes, signed_in, last in rows:
            attributes = attributes or ""
            # Kept apart for one site's frames, or a private window's: nobody's sign-in.
            if "partitionKey" in attributes or "privateBrowsingId" in attributes:
                continue
            container = _container_id(attributes)
            found.append(
                Source(
                    profile_name=name,
                    container_name=_container_name(profile, container) if container > 0 else "",
                    database=profile / "cookies.sqlite",
                    origin_attributes=attributes,
                    signed_in=bool(signed_in),
                    last_used=_seconds(last or 0),
                )
            )
    found.sort(key=lambda source: not source.signed_in)
    return found


def sign_in(source: Source, now: int | None = None) -> dict:
    """What StrmQt takes (firefox::carried): the label and the source's cookies
    for youtube.com and google.com, as moz_cookies rows."""
    now = int(time.time()) if now is None else now
    with _Snapshot(source.database) as db:
        rows = db.execute(
            "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite"
            " FROM moz_cookies WHERE originAttributes = ?",
            (source.origin_attributes,),
        ).fetchall()
    cookies = []
    for name, value, host, path, expiry, secure, http_only, same_site in rows:
        if not name or not _wanted(host or ""):
            continue
        expiry = _seconds(int(expiry or 0))
        if 0 < expiry <= now:
            continue
        cookies.append(
            {
                "name": name,
                "value": value or "",
                "host": host,
                "path": path or "/",
                "expiry": expiry,
                "isSecure": int(bool(secure)),
                "isHttpOnly": int(bool(http_only)),
                "sameSite": int(same_site or 0),
            }
        )
    return {"label": source.label, "cookies": cookies}
