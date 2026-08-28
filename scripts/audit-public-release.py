#!/usr/bin/env python3
"""Fail closed when the Git history is unsafe for a public release."""
from __future__ import annotations

import hashlib
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
    """Reject credential-shaped and non-source artifacts anywhere in a Git path."""
    lower = path.replace("\\", "/").lower()
    parts = tuple(part for part in lower.split("/") if part)
    name = parts[-1] if parts else ""

    if name != ".env.example" and (name == ".env" or name.startswith(".env.")):
        return True
    if any(part in {".direnv", ".runtime", "backups", "data"} for part in parts):
        return True
    if any(part in {".aws", ".gnupg", ".ssh", ".kube"} for part in parts):
        return True
    if name in {
        ".envrc",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account.json",
        "stage-fixture.json",
        "wallet.dat",
        "wallets.json",
    }:
        return True
    if name.startswith(("id_dsa", "id_ecdsa", "id_ed25519", "id_rsa")):
        return True
    if name.startswith("service-account") and name.endswith(".json"):
        return True
    return lower.endswith(
        (
            ".7z",
            ".bak",
            ".backup",
            ".cookie",
            ".db",
            ".db-shm",
            ".db-wal",
            ".key",
            ".log",
            ".mnemonic",
            ".p12",
            ".pem",
            ".rar",
            ".seed",
            ".sqlite",
            ".sqlite3",
            ".tar.gz",
            ".tgz",
            ".wallet",
            ".zip",
        )
    )


CONTENT_RULES = {
    "absolute local user path": re.compile(
        r"(?:/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|[A-Za-z]:\\Users\\[^\\\r\n]+\\)"
    ),
    "private-key block": re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    "ZIP-32 seed fingerprint": re.compile(r"zip32seedfp1[0-9a-z]{20,}", re.IGNORECASE),
    "GitHub access token": re.compile(
        r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"
    ),
    "GitLab access token": re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    "OpenAI API key": re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}"),
    "Slack access token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    "Stripe live key": re.compile(r"(?:sk|rk)_live_[A-Za-z0-9]{16,}"),
    "JWT credential": re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    "Bearer credential": re.compile(
        r"(?i)(?:authorization[\"']?\s*[:=]\s*[\"']?bearer\s+|bearer\s+)"
        r"[A-Za-z0-9._~+/-]{20,}"
    ),
    "AWS access key": re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    "real account UUID": re.compile(
        r"(?i)(?:account|wallet|operator|participant)[_-]?(?:id|uuid)[^\n]{0,30}"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    ),
    "wallet operation UUID in public fixture": re.compile(
        r'"operation_id"\s*:\s*"opid-[0-9a-f]{8}-[0-9a-f-]{27,}"', re.IGNORECASE
    ),
}

FULL_TESTNET_UA_RE = re.compile(
    r"(?i)\butest1[023456789acdefghjklmnpqrstuvwxyz]{45,512}\b"
)
# Official ZIP-316 test vector documented in the replay manifest; store only its digest here.
PUBLIC_TEST_VECTOR_UA_SHA256 = (
    "d543be4540067d241d861fba625723c0ae998f3c0cfb967cdcf0a66d07175efc"
)


def is_public_test_vector_ua(value: str) -> bool:
    return hashlib.sha256(value.lower().encode("ascii")).hexdigest() == PUBLIC_TEST_VECTOR_UA_SHA256


def scan_blob(path: str, data: bytes, failures: set[str]) -> None:
    # The policy implementation is the trust root; scanning its regex literals would
    # intentionally match every token signature. All other text and binary blobs are scanned.
    if path == SELF:
        return
    text = data.decode("utf-8", errors="ignore")
    for label, pattern in CONTENT_RULES.items():
        if pattern.search(text):
            failures.add(f"{label}: {path}")
    for match in FULL_TESTNET_UA_RE.finditer(text):
        if not is_public_test_vector_ua(match.group(0)):
            failures.add(f"full testnet Unified Address: {path}")


def main() -> int:
    failures: set[str] = set()
    refs = str(git("for-each-ref", "--format=%(refname)")).splitlines()
    for ref in refs:
        if any(ord(character) < 32 for character in ref):
            failures.add("control character in Git ref name")
        # Never echo a potentially live credential back in the failure label.
        scan_blob("<refname>", ref.encode("utf-8", errors="surrogateescape"), failures)

    tracked = [item for item in str(git("ls-files", "-z")).split("\0") if item]
    for path in tracked:
        if forbidden_path(path):
            failures.add(f"forbidden tracked path: {path}")
        if any(ord(character) < 32 for character in path):
            failures.add(f"control character in tracked path: {path!r}")
        candidate = ROOT / path
        if candidate.is_symlink():
            failures.add(f"tracked symbolic link is not allowed: {path}")
        elif candidate.is_file():
            scan_blob(path, candidate.read_bytes(), failures)

    # Walk every reachable commit tree. `rev-list --objects` can report only one
    # arbitrary path for a reused blob and therefore misses a secret file that was renamed.
    seen_blobs: set[str] = set()
    seen_trees: set[str] = set()

    def inspect_tree(tree_oid: str) -> None:
        if tree_oid in seen_trees:
            return
        seen_trees.add(tree_oid)
        listing = git("ls-tree", "-r", "-z", tree_oid, text=False)
        assert isinstance(listing, bytes)
        for raw_entry in listing.split(b"\0"):
            if not raw_entry:
                continue
            metadata, separator, raw_path = raw_entry.partition(b"\t")
            if not separator:
                failures.add(f"malformed historical tree entry: {tree_oid}")
                continue
            mode, kind, oid = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
            if any(ord(character) < 32 for character in path):
                failures.add(f"control character in historical path: {path!r}")
            if forbidden_path(path):
                failures.add(f"forbidden historical path: {path}")
            if mode == "120000":
                failures.add(f"historical symbolic link is not allowed: {path}")
            if kind == "commit":
                failures.add(f"historical Git submodule is not allowed: {path}")
            if kind == "blob" and oid not in seen_blobs:
                seen_blobs.add(oid)
                scan_blob(path, git("cat-file", "blob", oid, text=False), failures)

    for tree_oid in set(str(git("log", "--all", "--format=%T", "HEAD")).splitlines()):
        inspect_tree(tree_oid)

    # Refs may point directly to a blob/tree/tag (not a commit). Scan every reachable
    # object even when Git has no pathname, and walk trees for path policy.
    seen_tag_oids: set[str] = set()
    for line in str(git("rev-list", "--objects", "--all", "HEAD")).splitlines():
        oid = line.partition(" ")[0]
        if not oid:
            continue
        kind = str(git("cat-file", "-t", oid)).strip()
        if kind == "tree":
            inspect_tree(oid)
        elif kind == "blob" and oid not in seen_blobs:
            seen_blobs.add(oid)
            scan_blob(f"<blob {oid}>", git("cat-file", "blob", oid, text=False), failures)
        elif kind == "tag" and oid not in seen_tag_oids:
            seen_tag_oids.add(oid)
            scan_blob(f"<tag {oid}>", git("cat-file", "tag", oid, text=False), failures)

    # Commit metadata is pushed too; it can leak local paths or tokens.
    for oid in str(git("rev-list", "--all", "HEAD")).splitlines():
        if str(git("cat-file", "-t", oid)).strip() == "commit":
            scan_blob(f"<commit {oid}>", git("cat-file", "commit", oid, text=False), failures)
    tag_oids = str(git("for-each-ref", "--format=%(objectname)", "refs/tags")).splitlines()
    for oid in tag_oids:
        if (
            oid not in seen_tag_oids
            and str(git("cat-file", "-t", oid)).strip() == "tag"
        ):
            seen_tag_oids.add(oid)
            scan_blob(f"<tag {oid}>", git("cat-file", "tag", oid, text=False), failures)

    fixture_path = ROOT / "fixtures" / "offline-replay.json"
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        sanitization = fixture["sanitization"]
        if "removed" not in sanitization["privacy"]:
            failures.add("offline fixture lacks an explicit address-redaction declaration")
        for item in fixture["items"]:
            if not str(item["refund_address"]).startswith("redacted-"):
                failures.add(f"offline fixture refund address is not redacted: {item['public_id']}")
            invoice_address = str(item["invoice_address"])
            if not (
                invoice_address.startswith("redacted-")
                or (
                    FULL_TESTNET_UA_RE.fullmatch(invoice_address)
                    and is_public_test_vector_ua(invoice_address)
                )
            ):
                failures.add(f"offline fixture invoice address is not redacted: {item['public_id']}")
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
