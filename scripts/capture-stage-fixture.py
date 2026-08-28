#!/usr/bin/env python3
"""Capture a mined, unused live submission as the one-shot stage manifest."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TXID_RE = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_ID_RE = re.compile(r"^[a-z2-9]{12}$")


def fail(message: str) -> None:
    print(f"[hushboard] capture ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def collect_wallet_evidence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Load the verifier's single fail-closed wallet receipt implementation."""
    verifier_path = ROOT / "scripts" / "verify-stage-fixture.py"
    spec = importlib.util.spec_from_file_location("hushboard_stage_fixture_verifier", verifier_path)
    if spec is None or spec.loader is None:  # pragma: no cover - fixed repository path.
        raise RuntimeError("stage verifier yuklenemedi")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    try:
        return verifier.collect_wallet_evidence(*args, **kwargs)
    except verifier.WalletEvidenceError as exc:
        raise RuntimeError(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("public_id")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "hushboard.db")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime" / "stage-fixture.json")
    args = parser.parse_args()
    if not PUBLIC_ID_RE.fullmatch(args.public_id):
        fail("public_id gecersiz; 12 kucuk harf/rakam olmali")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT public_id,title,invoice_address,memo,amount_zat,status,bond_txid,bond_pool,"
            "bond_output_index,bond_confirmations,bond_mined_height,moderation_decision,refund_txid,created_at "
            "FROM submissions WHERE public_id=?",
            (args.public_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        fail(f"SQLite okunamadi: {exc}")
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        fail("submission bulunamadi")
    if row["status"] != "moderation" or row["moderation_decision"] is not None or row["refund_txid"]:
        fail(f"submission kullanilmamis moderation durumunda degil: {row['status']}")
    if row["amount_zat"] != 1_000_000 or row["memo"] != f"HB1:{row['public_id']}":
        fail("amount/memo invariant bozuk")
    txid = row["bond_txid"]
    if not isinstance(txid, str) or not TXID_RE.fullmatch(txid.lower()):
        fail("bond txid gecersiz")
    txid = txid.lower()
    if not isinstance(row["invoice_address"], str):
        fail("invoice receiver DB'de eksik")
    try:
        evidence = collect_wallet_evidence(
            txid,
            receiver=row["invoice_address"],
            amount_zat=1_000_000,
            memo=row["memo"],
            pool=row["bond_pool"],
            output_index=row["bond_output_index"],
            mined_height=row["bond_mined_height"],
        )
    except RuntimeError as exc:
        fail(str(exc))
    manifest = {
        "schema": "hushboard-stage-fixture/v1",
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": "zcash-testnet",
        "public_id": row["public_id"],
        "title": row["title"],
        "amount_zat": row["amount_zat"],
        "memo": row["memo"],
        "bond_txid": txid,
        "bond_pool": evidence["pool"],
        "bond_output_index": evidence["output_index"],
        "mined_height": evidence["mined_height"],
        "bond_blockhash": evidence["blockhash"],
        "confirmations_at_capture": evidence["confirmations"],
        "created_at": row["created_at"],
        "one_shot": True,
        "claim": "wallet-proven exact receiver/amount/memo; mined inbound; refund not yet launched",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(args.output)
    print(
        f"[hushboard] stage fixture captured: #{row['public_id']} tx={txid[:10]}…{txid[-8:]} "
        f"height={evidence['mined_height']} exact wallet output proven"
    )


if __name__ == "__main__":
    main()
