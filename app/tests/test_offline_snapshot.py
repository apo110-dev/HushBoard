from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from zcash_uri import TransactionRequest

from app.config import Settings
from app.service import FeatureDisabled, HushBoardService


def test_offline_loader_refuses_source_and_protected_live_database(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "capture.json"
    source.write_bytes((root / "fixtures" / "offline-replay.json").read_bytes())

    script = root / "scripts" / "load-offline-snapshot.py"
    same_path = subprocess.run(
        [sys.executable, str(script), "--snapshot", str(source), "--db", str(source)],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert same_path.returncode != 0
    assert "snapshot source" in same_path.stderr

    protected = tmp_path / "live.db"
    protected.write_bytes(b"do-not-replace")
    protected_run = subprocess.run(
        [
            sys.executable, str(script), "--snapshot", str(source), "--db", str(protected),
            "--protect-db", str(protected),
        ],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert protected_run.returncode != 0
    assert "protected live" in protected_run.stderr
    assert protected.read_bytes() == b"do-not-replace"

def test_checked_in_offline_snapshot_is_separate_labeled_and_read_only(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "offline-replay.db"
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "load-offline-snapshot.py"),
            "--snapshot", str(root / "fixtures" / "offline-replay.json"),
            "--db", str(database),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    settings = Settings.from_env({
        "HUSHBOARD_MODE": "mock",
        "HUSHBOARD_DB": str(database),
        "HUSHBOARD_WATCH_INTERVAL": "0",
    }, root=root)
    service = HushBoardService(settings)
    health = service.health()
    assert health["mode"] == "mock"
    assert health["snapshot"]["label"] == "OFFLINE REPLAY · NO LIVE SENDS"
    assert health["snapshot"]["captured_at"].endswith("Z")
    assert health["snapshot"]["block_height"] > 0
    assert health["explorer_base_url"] is None
    listing = service.list_submissions(status=None, query=None, limit=50, offset=0)
    assert listing["total"] >= 3
    assert all(row["demo"] and row["mode"] == "mock" for row in listing["items"])
    assert all(row["bond"]["explorer_url"] is None for row in listing["items"])
    assert all(not row["can_demo_send"] and not row["can_moderate"] for row in listing["items"])
    evidence = {row["id"]: row["evidence_kind"] for row in listing["items"]}
    assert evidence == {
        "g9825ru2tr4f": "real_confirmed_bond",
        "yfqyv6vsm48e": "real_confirmed_refund_e2e",
        "spamlink3377": "synthetic_walkthrough",
    }

    real_capture = service.get_submission("g9825ru2tr4f")
    parsed_payment = TransactionRequest.from_uri(real_capture["invoice"]["uri"]).payments()[0]
    assert parsed_payment.amount_zatoshis == 1_000_000
    assert parsed_payment.memo_text == "HB1:g9825ru2tr4f"

    before = [(row["id"], row["status"], row["updated_at"]) for row in listing["items"]]
    sync_result = service.sync()
    assert sync_result["read_only"] is True
    after_listing = service.list_submissions(status=None, query=None, limit=50, offset=0)
    after = [(row["id"], row["status"], row["updated_at"]) for row in after_listing["items"]]
    assert after == before

    first_id = listing["items"][0]["id"]
    mutations = (
        lambda: service.create_submission("No", "Mutation", "utest1" + "q" * 120),
        lambda: service.demo_send(first_id),
        lambda: service.moderate(first_id, decision="keep", note=None),
        lambda: service.seed(reset=False, count=1),
        service.reset,
    )
    for mutate in mutations:
        with pytest.raises(FeatureDisabled, match="immutable"):
            mutate()
