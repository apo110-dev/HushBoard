from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "hushboard_public_release_audit", ROOT / "scripts" / "audit-public-release.py"
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "nested/.env.production",
        "nested/.ssh/id_ed25519",
        "nested/.runtime/receipt.txt",
        "nested/.direnv/secret",
        "nested/backups/wallet.txt",
        "nested/data/export.json",
        ".envrc",
        "credentials.json",
        "secrets.yaml",
        "service-account-production.json",
        "wallet.dat",
        "release.zip",
        "data/safe-looking.txt",
    ],
)
def test_forbidden_paths_are_rejected(path: str) -> None:
    assert AUDIT.forbidden_path(path)


@pytest.mark.parametrize("path", [".env.example", "fixtures/offline-replay.json", "static/app.js"])
def test_public_paths_are_allowed(path: str) -> None:
    assert not AUDIT.forbidden_path(path)


def test_scan_blob_checks_nul_containing_binary_data() -> None:
    token = ("gh" + "p_" + "a" * 30).encode()
    failures: set[str] = set()

    AUDIT.scan_blob("binary.dat", b"prefix\x00" + token + b"\x00suffix", failures)

    assert any("GitHub access token" in failure for failure in failures)


def test_unknown_full_unified_address_is_rejected() -> None:
    address = "utest1" + "q" * 104
    failures: set[str] = set()

    AUDIT.scan_blob("fixture.json", address.encode(), failures)

    assert any("full testnet Unified Address" in failure for failure in failures)


def test_documented_zip316_vector_is_the_only_allowed_full_address() -> None:
    fixture = json.loads((ROOT / "fixtures" / "offline-replay.json").read_text())
    address = fixture["items"][0]["invoice_address"]
    failures: set[str] = set()

    assert AUDIT.is_public_test_vector_ua(address)
    AUDIT.scan_blob("fixture.json", address.encode(), failures)

    assert not failures


def test_history_scan_rejects_forbidden_path_after_safe_rename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )
    (repo / ".env").write_text("SAFE_PLACEHOLDER=1\n")

    def run_git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    run_git("init", "-b", "main")
    run_git("config", "user.name", "Audit Test")
    run_git("config", "user.email", "audit@example.invalid")
    run_git("add", ".")
    run_git("commit", "-m", "add historical configuration")
    run_git("mv", ".env", ".env.example")
    run_git("commit", "-m", "rename configuration")

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "forbidden historical path: .env" in completed.stderr


def test_reachable_blob_tag_is_content_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "tagged-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )

    def git_result(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )

    git_result("init", "-b", "main")
    git_result("config", "user.name", "Audit Test")
    git_result("config", "user.email", "audit@example.invalid")
    git_result("add", ".")
    git_result("commit", "-m", "safe public tree")
    token = "gh" + "p_" + "a" * 30
    blob_oid = git_result("hash-object", "-w", "--stdin", input_text=token).stdout.strip()
    git_result("tag", "-a", "blobtag", blob_oid, "-m", "reachable blob fixture")

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "GitHub access token: <blob" in completed.stderr


def test_reachable_tree_tag_is_path_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "tagged-tree-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )

    def git_result(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )

    git_result("init", "-b", "main")
    git_result("config", "user.name", "Audit Test")
    git_result("config", "user.email", "audit@example.invalid")
    git_result("add", ".")
    git_result("commit", "-m", "safe public tree")
    blob_oid = git_result(
        "hash-object", "-w", "--stdin", input_text="SAFE_PLACEHOLDER=1\n"
    ).stdout.strip()
    tree_oid = git_result(
        "mktree", input_text=f"100644 blob {blob_oid}\t.env\n"
    ).stdout.strip()
    git_result("tag", "-a", "treetag", tree_oid, "-m", "reachable tree fixture")

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "forbidden historical path: .env" in completed.stderr


def test_annotated_tag_metadata_on_custom_ref_is_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "custom-ref-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )

    def git_result(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        )

    git_result("init", "-b", "main")
    git_result("config", "user.name", "Audit Test")
    git_result("config", "user.email", "audit@example.invalid")
    git_result("add", ".")
    git_result("commit", "-m", "safe public tree")
    token = "gh" + "p_" + "a" * 30
    git_result("tag", "-a", "temporary-tag", "HEAD", "-m", token)
    tag_oid = git_result("rev-parse", "refs/tags/temporary-tag").stdout.strip()
    git_result("update-ref", "refs/releases/custom", tag_oid)
    git_result("tag", "-d", "temporary-tag")

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "GitHub access token: <tag" in completed.stderr


def test_git_ref_names_are_content_scanned_without_echoing_secret(tmp_path: Path) -> None:
    repo = tmp_path / "secret-ref-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )

    def git_result(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        )

    git_result("init", "-b", "main")
    git_result("config", "user.name", "Audit Test")
    git_result("config", "user.email", "audit@example.invalid")
    git_result("add", ".")
    git_result("commit", "-m", "safe public tree")
    token = "gh" + "p_" + "a" * 30
    git_result("branch", token)

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "GitHub access token: <refname>" in completed.stderr
    assert token not in completed.stderr


def test_detached_head_history_is_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "detached-head-repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "fixtures").mkdir()
    shutil.copy2(ROOT / "scripts" / "audit-public-release.py", repo / "scripts")
    (repo / "fixtures" / "offline-replay.json").write_text(
        json.dumps(
            {
                "sanitization": {"privacy": "addresses removed"},
                "items": [],
            }
        )
    )

    def git_result(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            capture_output=True,
            check=True,
        )

    git_result("init", "-b", "main")
    git_result("config", "user.name", "Audit Test")
    git_result("config", "user.email", "audit@example.invalid")
    git_result("add", ".")
    git_result("commit", "-m", "safe root")
    root_oid = git_result("rev-parse", "HEAD").stdout.strip()

    token = "gh" + "p_" + "a" * 30
    (repo / "temporary.txt").write_text(token)
    git_result("add", "temporary.txt")
    git_result("commit", "-m", "temporary credential")
    (repo / "temporary.txt").unlink()
    git_result("add", "-u")
    git_result("commit", "-m", "remove credential")
    git_result("checkout", "--detach", "HEAD")
    git_result("update-ref", "refs/heads/main", root_oid)

    completed = subprocess.run(
        [sys.executable, str(repo / "scripts" / "audit-public-release.py")],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "GitHub access token: temporary.txt" in completed.stderr
