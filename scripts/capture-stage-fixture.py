#!/usr/bin/env python3
"""Capture a mined, unused live submission as the one-shot stage manifest."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"[hushboard] capture ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("public_id")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "hushboard.db")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime" / "stage-fixture.json")
    args = parser.parse_args()
    if len(args.public_id) != 12:
        fail("public_id 12 karakter olmali")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT public_id,title,memo,amount_zat,status,bond_txid,bond_pool,bond_output_index,"
            "bond_confirmations,bond_mined_height,moderation_decision,refund_txid,created_at "
            "FROM submissions WHERE public_id=?",
            (args.public_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        fail("submission bulunamadi")
    if row["status"] != "moderation" or row["moderation_decision"] is not None or row["refund_txid"]:
        fail(f"submission kullanilmamis moderation durumunda degil: {row['status']}")
    if row["amount_zat"] != 1_000_000 or row["memo"] != f"HB1:{row['public_id']}":
        fail("amount/memo invariant bozuk")
    txid = row["bond_txid"]
    rpc = subprocess.run(
        [str(ROOT / "scripts" / "wallet-rpc.sh"), "operator", "z_viewtransaction", json.dumps([txid])],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if rpc.returncode:
        fail("wallet receipt okunamadi")
    try:
        view = json.loads(rpc.stdout).get("result") or {}
    except json.JSONDecodeError:
        fail("wallet receipt JSON gecersiz")
    if view.get("status") != "mined" or not isinstance(view.get("confirmations"), int) or view["confirmations"] < 1:
        fail("wallet receipt mined degil")
    manifest = {
        "schema": "hushboard-stage-fixture/v1",
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "network": "zcash-testnet",
        "public_id": row["public_id"],
        "title": row["title"],
        "amount_zat": row["amount_zat"],
        "memo": row["memo"],
        "bond_txid": txid,
        "bond_pool": row["bond_pool"],
        "bond_output_index": row["bond_output_index"],
        "mined_height": row["bond_mined_height"],
        "confirmations_at_capture": view["confirmations"],
        "created_at": row["created_at"],
        "one_shot": True,
        "claim": "real exact bond; confirmed inbound; refund not yet launched",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(args.output)
    print(f"[hushboard] stage fixture captured: #{row['public_id']} tx={txid[:10]}…{txid[-8:]}")


if __name__ == "__main__":
    main()
