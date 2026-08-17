#!/usr/bin/env python3
"""Fail-closed verification for the one-shot live stage fixture."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def die(message: str) -> None:
    print(f"[hushboard] stage fixture ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / ".runtime" / "stage-fixture.json")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "hushboard.db")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"manifest okunamadi: {exc}")
    if (
        manifest.get("schema") != "hushboard-stage-fixture/v1"
        or manifest.get("network") != "zcash-testnet"
        or manifest.get("one_shot") is not True
    ):
        die("schema/network/one_shot invariant gecersiz")
    public_id = manifest.get("public_id")
    txid = manifest.get("bond_txid")
    if not isinstance(public_id, str) or len(public_id) != 12:
        die("public_id gecersiz")
    if not isinstance(txid, str) or len(txid) != 64:
        die("bond_txid gecersiz")
    if manifest.get("amount_zat") != 1_000_000 or manifest.get("memo") != f"HB1:{public_id}":
        die("amount/memo invariant eslesmiyor")

    rpc = subprocess.run(
        [str(ROOT / "scripts" / "wallet-rpc.sh"), "operator", "z_viewtransaction", json.dumps([txid])],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if rpc.returncode:
        die("operator wallet receipt okunamadi")
    try:
        view = json.loads(rpc.stdout).get("result") or {}
    except json.JSONDecodeError:
        die("operator wallet receipt JSON gecersiz")
    confirmations = view.get("confirmations")
    if view.get("status") != "mined" or not isinstance(confirmations, int) or confirmations < 1:
        die(f"bond mined degil (status={view.get('status')}, confirmations={confirmations})")

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT public_id,memo,amount_zat,status,bond_txid,bond_pool,bond_output_index,"
            "bond_mined_height,bond_confirmations,moderation_decision,refund_txid "
            "FROM submissions WHERE public_id=?",
            (public_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        die(f"SQLite okunamadi: {exc}")
    finally:
        if conn is not None:
            conn.close()
    if row is None:
        die("submission DB'de yok")
    if (
        row["memo"] != manifest["memo"]
        or row["amount_zat"] != 1_000_000
        or row["bond_txid"] != txid
        or row["bond_pool"] != manifest.get("bond_pool")
        or row["bond_output_index"] != manifest.get("bond_output_index")
        or row["bond_mined_height"] != manifest.get("mined_height")
    ):
        die("DB receipt manifest ile eslesmiyor")
    if row["status"] != "moderation" or row["moderation_decision"] is not None or row["refund_txid"] is not None:
        die(f"fixture tek kullanim icin hazir degil (status={row['status']})")
    if row["bond_confirmations"] < 1:
        die("DB confirmation kaniti eksik")
    print(
        f"[hushboard] stage fixture OK: #{public_id} tx={txid[:10]}…{txid[-8:]} "
        f"confirmations={confirmations}"
    )


if __name__ == "__main__":
    main()
