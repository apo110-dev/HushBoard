#!/usr/bin/env python3
"""Replace the offline DB with a checked-in, immutable replay snapshot.

This script never calls wallet RPC and never broadcasts. It deliberately writes a disposable
SQLite copy; the service treats that copy as read-only and the source JSON is never altered.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import Database


def zip321(address: str, amount: str, memo: str) -> str:
    encoded = base64.urlsafe_b64encode(memo.encode("utf-8")).decode("ascii").rstrip("=")
    return f"zcash:{address}?amount={amount}&memo={encoded}"


def load(
    snapshot_path: Path,
    database_path: Path,
    *,
    protected_paths: tuple[Path, ...] = (),
) -> dict:
    snapshot_path = snapshot_path.resolve()
    database_path = database_path.resolve()
    protected = {
        (REPO_ROOT / "data" / "hushboard.db").resolve(),
        *(path.resolve() for path in protected_paths),
    }
    if database_path == snapshot_path:
        raise SystemExit("refusing to overwrite the offline snapshot source")
    if database_path in protected:
        raise SystemExit("refusing to replace a protected live database")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("schema") != "hushboard-offline-replay/v1" or snapshot.get("immutable") is not True:
        raise SystemExit("refusing an unversioned or mutable offline snapshot")
    captured_at = snapshot["captured_at"]
    items = snapshot.get("items")
    if not isinstance(items, list) or not items:
        raise SystemExit("offline snapshot contains no items")

    for suffix in ("", "-wal", "-shm"):
        try:
            (Path(str(database_path) + suffix)).unlink()
        except FileNotFoundError:
            pass
    db = Database(database_path)
    db.initialize()

    for item in items:
        public_id = item["public_id"]
        memo = f"HB1:{public_id}"
        address = item["invoice_address"]
        amount_zat = int(item.get("amount_zat", 1_000_000))
        if len(public_id) != 12 or amount_zat != 1_000_000:
            raise SystemExit("invalid replay invoice invariant")
        values = {
            "public_id": public_id,
            "title": item["title"],
            "body": item["body"],
            "refund_address": item["refund_address"],
            "refund_address_hint": item["refund_address_hint"],
            "invoice_address": address,
            "invoice_diversifier_index": item.get("invoice_diversifier_index"),
            "zip321_uri": zip321(address, "0.01000000", memo),
            "memo": memo,
            "amount_zat": amount_zat,
            "status": "awaiting_bond",
            "demo": 1,
        }
        row = db.create_submission(values, now=captured_at)
        target = item["status"]
        bond = item.get("bond") or {}
        if bond:
            txid = bond["txid"]
            pool = bond.get("pool", "ironwood")
            index = int(bond.get("output_index", 0))
            confirmations = int(bond.get("confirmations", 0))
            tx_status = bond.get("tx_status", "waiting")
            with db.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO wallet_outputs "
                    "(txid,pool,output_index,submission_id,to_address,value_zat,memo,is_change,mined_height,"
                    "confirmations,tx_status,match_result,mismatch_reason,first_seen_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)",
                    (
                        txid, pool, index, row["id"], address, int(bond.get("value_zat", amount_zat)),
                        bond.get("memo", memo), bond.get("mined_height"), confirmations, tx_status,
                        bond.get("match_result", "exact"), bond.get("mismatch_reason"), captured_at, captured_at,
                    ),
                )
            if target == "mismatch":
                db.transition(
                    public_id, "mismatch", reason="offline_snapshot_mismatch", now=captured_at,
                    updates={
                        "mismatch_reason": bond.get("mismatch_reason", "snapshot_mismatch"),
                        "status_detail": item.get("status_detail", "OFFLINE REPLAY snapshot mismatch."),
                    },
                )
                continue
            db.transition(
                public_id, "bond_pending", reason="offline_snapshot_bond", now=captured_at,
                updates={
                    "bond_txid": txid, "bond_pool": pool, "bond_output_index": index,
                    "bond_confirmations": confirmations, "bond_mined_height": bond.get("mined_height"),
                    "bond_tx_status": tx_status, "status_detail": item.get("status_detail"),
                },
            )
        if target in {"moderation", "kept", "refund_broadcast", "refunded"}:
            db.transition(
                public_id, "moderation", reason="offline_snapshot_confirmed", now=captured_at,
                updates={"status_detail": item.get("status_detail", "OFFLINE REPLAY confirmed bond snapshot.")},
            )
        if target == "kept":
            db.transition(
                public_id, "kept", reason="offline_snapshot_kept", now=captured_at,
                updates={
                    "moderation_decision": "keep", "moderated_at": captured_at,
                    "moderation_note": "OFFLINE REPLAY policy decision; no live send.",
                    "status_detail": "OFFLINE REPLAY: bond kept in the recorded snapshot.",
                },
            )
        elif target in {"refund_broadcast", "refunded"}:
            refund = item["refund"]
            opid = refund["operation_id"]
            rtxid = refund["txid"]
            with db.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO operations "
                    "(operation_id,submission_id,kind,wallet_role,status,txid,txids_json,broadcast,created_at,updated_at) "
                    "VALUES (?,?,'refund','mock','success',?,?,1,?,?)",
                    (opid, row["id"], rtxid, json.dumps([rtxid]), captured_at, captured_at),
                )
            db.transition(
                public_id, "refund_broadcast", reason="offline_snapshot_refund", now=captured_at,
                updates={
                    "moderation_decision": "refund", "moderated_at": captured_at,
                    "moderation_note": "OFFLINE REPLAY recorded decision.",
                    "refund_operation_id": opid, "refund_txid": rtxid,
                    "refund_confirmations": int(refund.get("confirmations", 0)),
                    "refund_tx_status": refund.get("tx_status", "waiting"),
                    "status_detail": "OFFLINE REPLAY: recorded refund broadcast.",
                },
            )
            if target == "refunded":
                db.transition(
                    public_id, "refunded", reason="offline_snapshot_refund_confirmed", now=captured_at,
                    updates={
                        "refund_confirmations": int(refund["confirmations"]),
                        "refund_tx_status": "mined",
                        "status_detail": "OFFLINE REPLAY: recorded refund confirmation.",
                    },
                )
        elif target == "failure":
            db.transition(
                public_id, "failure", reason="offline_snapshot_failure", now=captured_at,
                updates={"status_detail": item.get("status_detail", "OFFLINE REPLAY recorded failure.")},
            )

    evidence_kinds = {}
    for item in items:
        kind = item.get("evidence_kind")
        if not isinstance(kind, str) or not kind.replace("_", "").isalnum() or len(kind) > 64:
            raise SystemExit("offline snapshot item has an invalid evidence_kind")
        evidence_kinds[item["public_id"]] = kind

    public_meta = {
        "schema": snapshot["schema"],
        "immutable": True,
        "captured_at": captured_at,
        "network": snapshot["network"],
        "block_height": snapshot["block_height"],
        "label": "OFFLINE REPLAY · NO LIVE SENDS",
        "evidence": snapshot.get("evidence", {}),
    }
    db.set_meta("offline_snapshot", json.dumps(public_meta, separators=(",", ":")), now=captured_at)
    db.set_meta("offline_evidence_kinds", json.dumps(evidence_kinds, separators=(",", ":")), now=captured_at)
    db.set_meta("last_sync_at", captured_at, now=captured_at)
    return {"loaded": len(items), **public_meta}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--protect-db", type=Path, action="append", default=[])
    args = parser.parse_args()
    result = load(
        args.snapshot.resolve(),
        args.db.resolve(),
        protected_paths=tuple(path.resolve() for path in args.protect_db),
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
