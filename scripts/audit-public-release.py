#!/usr/bin/env python3
"""Fail closed when the Git history is unsafe for a public release."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/audit-public-release.py"


def git(*args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return completed.stdout


def forbidden_path(path: str) -> bool:
    lower = path.lower()
    name = Path(lower).name
    if lower == ".env":
        return True
    if lower.startswith((".runtime/", "data/", "backups/")):
        return True
    if name in {"wallets.json", "stage-fixture.json"}:
        return True
    return lower.endswith((
        ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".cookie",
        ".seed", ".mnemonic", ".wallet", ".pem", ".key", ".p12", ".tgz",
        ".tar.gz", ".log",
    ))


CONTENT_RULES = {
    "absolute local home path": re.compile(r"/home/[A-Za-z0-9._-]+/"),
    "private-key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "ZIP-32 seed fingerprint": re.compile(r"zip32seedfp1[0-9a-z]{20,}", re.IGNORECASE),
    "GitHub access token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "real account UUID": re.compile(
        r"(?i)(?:account|wallet|operator|participant)[_-]?(?:id|uuid)[^\n]{0,30}"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    ),
    "full refund Unified Address": re.compile(r'"refund_address"\s*:\s*"utest1[0-9a-z]{45,}"'),
    "wallet operation UUID in public fixture": re.compile(
        r'"operation_id"\s*:\s*"opid-[0-9a-f]{8}-[0-9a-f-]{27,}"', re.IGNORECASE
    ),
}


def scan_blob(path: str, data: bytes, failures: set[str]) -> None:
    if path == SELF or b"\x00" in data:
        return
    text = data.decode("utf-8", errors="ignore")
    for label, pattern in CONTENT_RULES.items():
        if pattern.search(text):
            failures.add(f"{label}: {path}")


def main() -> int:
    failures: set[str] = set()
    tracked = [item for item in str(git("ls-files", "-z")).split("\0") if item]
    for path in tracked:
        if forbidden_path(path):
            failures.add(f"forbidden tracked path: {path}")
        candidate = ROOT / path
        if candidate.is_file():
            scan_blob(path, candidate.read_bytes(), failures)

    # A normal push includes every object reachable from refs, not only the current tree.
    seen: set[str] = set()
    for line in str(git("rev-list", "--objects", "--all")).splitlines():
        oid, _, path = line.partition(" ")
        if not path or oid in seen:
            continue
        seen.add(oid)
        kind = str(git("cat-file", "-t", oid)).strip()
        if kind != "blob":
            continue
        if forbidden_path(path):
            failures.add(f"forbidden historical path: {path}")
        scan_blob(path, git("cat-file", "blob", oid, text=False), failures)

    fixture_path = ROOT / "fixtures" / "offline-replay.json"
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        sanitization = fixture["sanitization"]
        if "removed" not in sanitization["privacy"]:
            failures.add("offline fixture lacks an explicit address-redaction declaration")
        for item in fixture["items"]:
            if not str(item["refund_address"]).startswith("redacted-"):
                failures.add(f"offline fixture refund address is not redacted: {item['public_id']}")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        failures.add(f"offline fixture sanitization cannot be verified: {type(exc).__name__}")

    dirty = str(git("status", "--porcelain"))
    if dirty:
        failures.add("working tree is not clean")

    if failures:
        print("PUBLIC RELEASE AUDIT FAILED", file=sys.stderr)
        for failure in sorted(failures):
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(f"PUBLIC RELEASE AUDIT OK: {len(tracked)} tracked files; reachable history scanned")
    print("Create uploads with `git archive`, never by archiving the working directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
