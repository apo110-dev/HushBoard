#!/usr/bin/env python3
"""Fail-closed verification for the one-shot live stage fixture."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TXID_RE = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_ID_RE = re.compile(r"^[a-z2-9]{12}$")
DECIMAL_ZEC_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,8})?$")
MAX_MONEY_ZAT = 21_000_000 * 100_000_000
RPC_PAGE_SIZE = 500
RPC_MAX_PAGES = 100

RpcRunner = Callable[..., subprocess.CompletedProcess[str]]


class WalletEvidenceError(RuntimeError):
    """The wallet did not return complete, internally consistent bond evidence."""


def die(message: str) -> None:
    print(f"[hushboard] stage fixture ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def _strict_decimal(token: str) -> Decimal:
    # JSON permits exponent notation, but Zallet's JsonZec contract is a fixed-point
    # decimal with at most eight places. Rejecting other spellings avoids accidental
    # float/rounding semantics in evidence code.
    if not DECIMAL_ZEC_RE.fullmatch(token):
        raise ValueError("non-canonical ZEC amount")
    return Decimal(token)


def _decode_rpc(stdout: str, method: str) -> Any:
    try:
        envelope = json.loads(stdout, parse_float=_strict_decimal)
    except (json.JSONDecodeError, ValueError, InvalidOperation) as exc:
        raise WalletEvidenceError(f"{method} wallet response JSON gecersiz") from exc
    if not isinstance(envelope, dict):
        raise WalletEvidenceError(f"{method} wallet response envelope gecersiz")
    if envelope.get("error") is not None:
        raise WalletEvidenceError(f"{method} wallet RPC hata dondurdu")
    if "result" not in envelope or envelope["result"] is None:
        raise WalletEvidenceError(f"{method} wallet result eksik")
    return envelope["result"]


def _wallet_rpc(method: str, params: list[Any], *, runner: RpcRunner = subprocess.run) -> Any:
    try:
        completed = runner(
            [str(ROOT / "scripts" / "wallet-rpc.sh"), "operator", method, json.dumps(params)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WalletEvidenceError(f"{method} operator wallet RPC calistirilamadi") from exc
    if completed.returncode:
        raise WalletEvidenceError(f"{method} operator wallet RPC okunamadi")
    return _decode_rpc(completed.stdout, method)


def _strict_int(value: Any, label: str, *, minimum: int = 0, maximum: int = MAX_MONEY_ZAT) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise WalletEvidenceError(f"wallet evidence {label} integer gecersiz")
    return value


def _alias_value(obj: dict[str, Any], aliases: tuple[str, ...], label: str) -> Any:
    present = [(key, obj[key]) for key in aliases if key in obj and obj[key] is not None]
    if not present:
        raise WalletEvidenceError(f"wallet evidence {label} eksik")
    first = present[0][1]
    if any(type(value) is not type(first) or value != first for _, value in present[1:]):
        raise WalletEvidenceError(f"wallet evidence {label} aliaslari celisiyor")
    return first


def _optional_alias_value(obj: dict[str, Any], aliases: tuple[str, ...], label: str) -> Any | None:
    present = [(key, obj[key]) for key in aliases if key in obj and obj[key] is not None]
    if not present:
        return None
    first = present[0][1]
    if any(type(value) is not type(first) or value != first for _, value in present[1:]):
        raise WalletEvidenceError(f"wallet evidence {label} aliaslari celisiyor")
    return first


def _zec_value_to_zat(value: Any) -> int:
    if isinstance(value, (bool, float)):
        raise WalletEvidenceError("wallet evidence ZEC amount tipi gecersiz")
    if isinstance(value, int):
        decimal = Decimal(value)
    elif isinstance(value, Decimal):
        decimal = value
    elif isinstance(value, str) and DECIMAL_ZEC_RE.fullmatch(value):
        decimal = Decimal(value)
    else:
        raise WalletEvidenceError("wallet evidence ZEC amount formati gecersiz")
    if not decimal.is_finite() or decimal < 0:
        raise WalletEvidenceError("wallet evidence ZEC amount araligi gecersiz")
    scaled = decimal * Decimal(100_000_000)
    if scaled != scaled.to_integral_value():
        raise WalletEvidenceError("wallet evidence amount tam zatoshi degil")
    return _strict_int(int(scaled), "amount")


def _memo_candidates(output: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("memoStr", "memo_str"):
        if key in output and output[key] is not None:
            if not isinstance(output[key], str):
                raise WalletEvidenceError("wallet evidence memo text tipi gecersiz")
            candidates.append(output[key].rstrip("\x00"))
    if "memo" in output and output["memo"] is not None:
        raw = output["memo"]
        if not isinstance(raw, str):
            raise WalletEvidenceError("wallet evidence memo tipi gecersiz")
        if raw.startswith("HB1:"):
            candidates.append(raw.rstrip("\x00"))
        else:
            try:
                decoded = bytes.fromhex(raw).rstrip(b"\x00").decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise WalletEvidenceError("wallet evidence memo decode edilemiyor") from exc
            candidates.append(decoded)
    if not candidates:
        raise WalletEvidenceError("wallet evidence memo eksik")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise WalletEvidenceError("wallet evidence memo alanlari celisiyor")
    return candidates


def _output_pool(output: dict[str, Any]) -> str:
    pool = _alias_value(output, ("pool",), "pool")
    if not isinstance(pool, str):
        raise WalletEvidenceError("wallet evidence pool tipi gecersiz")
    return pool.lower()


def _output_index(output: dict[str, Any], pool: str, *, listed: bool) -> int:
    aliases: tuple[str, ...]
    if listed:
        aliases = ("output_index", "outputIndex")
    elif pool == "transparent":
        aliases = ("tOut", "t_out", "output_index", "outputIndex")
    elif pool == "sapling":
        aliases = ("output", "output_index", "outputIndex")
    else:
        aliases = ("action", "output_index", "outputIndex")
    return _strict_int(_alias_value(output, aliases, "output index"), "output index", maximum=2**32 - 1)


def _output_address(output: dict[str, Any], *, listed: bool) -> str:
    aliases = ("to_address", "toAddress", "address") if listed else ("address", "to_address", "toAddress")
    address = _alias_value(output, aliases, "receiver")
    if not isinstance(address, str) or not address.startswith("utest1") or len(address) > 512:
        raise WalletEvidenceError("wallet evidence receiver gecersiz")
    return address


def _output_amount_zat(output: dict[str, Any], *, listed: bool) -> int:
    explicit = _optional_alias_value(output, ("valueZat", "value_zat"), "amount")
    if explicit is not None:
        amount = _strict_int(explicit, "amount")
        if "value" in output and not listed:
            decimal_amount = _zec_value_to_zat(output["value"])
            if decimal_amount != amount:
                raise WalletEvidenceError("wallet evidence ZEC/zatoshi amount celisiyor")
        return amount
    if "value" not in output:
        raise WalletEvidenceError("wallet evidence amount eksik")
    if listed:
        # Zallet beta z_listtransactions defines `value` as integer zatoshis.
        return _strict_int(output["value"], "amount")
    # Zallet beta z_viewtransaction defines `value` as decimal ZEC.
    return _zec_value_to_zat(output["value"])


def _matching_view_output(
    outputs: Any,
    *,
    receiver: str,
    amount_zat: int,
    memo: str,
    pool: str,
    output_index: int,
) -> dict[str, Any]:
    if not isinstance(outputs, list) or not outputs or not all(isinstance(item, dict) for item in outputs):
        raise WalletEvidenceError("z_viewtransaction wallet outputs eksik/gecersiz")
    matches: list[dict[str, Any]] = []
    for output in outputs:
        candidate_pool = _output_pool(output)
        candidate_index = _output_index(output, candidate_pool, listed=False)
        if candidate_pool != pool or candidate_index != output_index:
            continue
        candidate_memos = _memo_candidates(output)
        candidate_address = _output_address(output, listed=False)
        candidate_amount = _output_amount_zat(output, listed=False)
        outgoing = _alias_value(output, ("outgoing",), "outgoing flag")
        wallet_internal = _alias_value(output, ("walletInternal", "wallet_internal"), "wallet internal flag")
        account_uuid = _alias_value(output, ("account_uuid", "accountUuid"), "receiving account")
        if not isinstance(outgoing, bool) or outgoing is not False:
            raise WalletEvidenceError("wallet evidence output inbound degil")
        if not isinstance(wallet_internal, bool) or wallet_internal is not False:
            raise WalletEvidenceError("wallet evidence output external receiver degil")
        if not isinstance(account_uuid, str) or not account_uuid:
            raise WalletEvidenceError("wallet evidence receiving account eksik")
        if candidate_address == receiver and candidate_amount == amount_zat and candidate_memos[0] == memo:
            matches.append(output)
    if len(matches) != 1:
        raise WalletEvidenceError("z_viewtransaction exact receiver/amount/memo output kaniti yok veya belirsiz")
    return matches[0]


def _matching_listed_output(
    transaction: dict[str, Any],
    *,
    receiver: str,
    amount_zat: int,
    memo: str,
    pool: str,
    output_index: int,
) -> dict[str, Any]:
    account_uuid = _alias_value(transaction, ("account_uuid", "accountUuid"), "transaction account")
    if not isinstance(account_uuid, str) or not account_uuid:
        raise WalletEvidenceError("z_listtransactions account UUID eksik")
    outputs = transaction.get("outputs")
    if not isinstance(outputs, list) or not outputs or not all(isinstance(item, dict) for item in outputs):
        raise WalletEvidenceError("z_listtransactions wallet outputs eksik/gecersiz")
    matches: list[dict[str, Any]] = []
    for output in outputs:
        candidate_pool = _output_pool(output)
        candidate_index = _output_index(output, candidate_pool, listed=True)
        if candidate_pool != pool or candidate_index != output_index:
            continue
        candidate_address = _output_address(output, listed=True)
        candidate_amount = _output_amount_zat(output, listed=True)
        candidate_memos = _memo_candidates(output)
        is_change = _alias_value(output, ("is_change", "isChange"), "change flag")
        to_account = _alias_value(output, ("to_account", "toAccount"), "output account")
        if not isinstance(is_change, bool) or is_change is not False:
            raise WalletEvidenceError("wallet evidence output change olarak isaretli")
        if not isinstance(to_account, str) or to_account != account_uuid:
            raise WalletEvidenceError("wallet evidence output operator hesabina ait degil")
        if candidate_address == receiver and candidate_amount == amount_zat and candidate_memos[0] == memo:
            matches.append(output)
    if len(matches) != 1:
        raise WalletEvidenceError("z_listtransactions exact receiver/amount/memo output kaniti yok veya belirsiz")
    return matches[0]


def _transaction_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        result = _alias_value(result, ("transactions", "items"), "transaction list")
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise WalletEvidenceError("z_listtransactions result shape gecersiz")
    return result


def collect_wallet_evidence(
    txid: str,
    *,
    receiver: str,
    amount_zat: int,
    memo: str,
    pool: str,
    output_index: int,
    mined_height: int,
    runner: RpcRunner = subprocess.run,
) -> dict[str, Any]:
    """Require two wallet RPC receipts to prove the exact, mined inbound bond.

    z_viewtransaction proves the wallet decrypted the exact incoming output and reports
    current mined status. z_listtransactions independently binds that output to a mined
    height, which beta z_viewtransaction does not expose.
    """
    txid = txid.lower() if isinstance(txid, str) else ""
    if not TXID_RE.fullmatch(txid):
        raise WalletEvidenceError("bond txid gecersiz")
    if not isinstance(receiver, str) or not receiver.startswith("utest1") or len(receiver) > 512:
        raise WalletEvidenceError("beklenen receiver gecersiz")
    amount_zat = _strict_int(amount_zat, "expected amount")
    if not isinstance(memo, str) or not memo.startswith("HB1:"):
        raise WalletEvidenceError("beklenen memo gecersiz")
    if not isinstance(pool, str) or pool.lower() not in {"orchard", "ironwood"}:
        raise WalletEvidenceError("beklenen pool gecersiz")
    pool = pool.lower()
    output_index = _strict_int(output_index, "expected output index", maximum=2**32 - 1)
    mined_height = _strict_int(mined_height, "expected mined height", minimum=1, maximum=2**32 - 1)

    view = _wallet_rpc("z_viewtransaction", [txid], runner=runner)
    if not isinstance(view, dict):
        raise WalletEvidenceError("z_viewtransaction result shape gecersiz")
    view_txid = view.get("txid")
    if not isinstance(view_txid, str) or view_txid.lower() != txid:
        raise WalletEvidenceError("z_viewtransaction txid eslesmiyor")
    status = view.get("status")
    confirmations = _strict_int(view.get("confirmations"), "confirmations", minimum=1, maximum=2**31 - 1)
    if not isinstance(status, str) or status.lower() != "mined":
        raise WalletEvidenceError("z_viewtransaction bond mined degil")
    blockhash = view.get("blockhash")
    if not isinstance(blockhash, str) or not TXID_RE.fullmatch(blockhash.lower()):
        raise WalletEvidenceError("z_viewtransaction mined blockhash eksik/gecersiz")
    _strict_int(view.get("blockindex"), "block index", maximum=2**32 - 1)
    _strict_int(view.get("blocktime"), "block time", minimum=1, maximum=2**63 - 1)
    _matching_view_output(
        view.get("outputs"),
        receiver=receiver,
        amount_zat=amount_zat,
        memo=memo,
        pool=pool,
        output_index=output_index,
    )

    matching_rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(RPC_MAX_PAGES):
        page = _transaction_rows(
            _wallet_rpc("z_listtransactions", [None, None, None, offset, RPC_PAGE_SIZE], runner=runner)
        )
        for transaction in page:
            listed_txid = transaction.get("txid")
            if not isinstance(listed_txid, str) or not TXID_RE.fullmatch(listed_txid.lower()):
                raise WalletEvidenceError("z_listtransactions gecersiz txid dondurdu")
            if listed_txid.lower() == txid:
                matching_rows.append(transaction)
        if matching_rows or len(page) < RPC_PAGE_SIZE:
            break
        offset += len(page)
    else:
        raise WalletEvidenceError("z_listtransactions tarama sinirini asti")
    if not matching_rows:
        raise WalletEvidenceError("bond z_listtransactions wallet history icinde yok")

    heights: set[int] = set()
    for transaction in matching_rows:
        height = _strict_int(
            _alias_value(transaction, ("mined_height", "minedHeight"), "mined height"),
            "mined height",
            minimum=1,
            maximum=2**32 - 1,
        )
        heights.add(height)
        if transaction.get("expired_unmined") is not False:
            raise WalletEvidenceError("z_listtransactions transaction mined olarak guvenli degil")
        received_note_count = _strict_int(
            _alias_value(transaction, ("received_note_count", "receivedNoteCount"), "received note count"),
            "received note count",
            minimum=1,
            maximum=2**32 - 1,
        )
        if received_note_count < 1:  # pragma: no cover - guarded by _strict_int, kept explicit.
            raise WalletEvidenceError("z_listtransactions received note kaniti yok")
        _matching_listed_output(
            transaction,
            receiver=receiver,
            amount_zat=amount_zat,
            memo=memo,
            pool=pool,
            output_index=output_index,
        )
    if heights != {mined_height}:
        raise WalletEvidenceError("wallet mined height manifest/DB ile eslesmiyor")

    return {
        "confirmations": confirmations,
        "mined_height": mined_height,
        "blockhash": blockhash.lower(),
        "pool": pool,
        "output_index": output_index,
    }


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
    if not isinstance(public_id, str) or not PUBLIC_ID_RE.fullmatch(public_id):
        die("public_id gecersiz")
    if not isinstance(txid, str) or not TXID_RE.fullmatch(txid.lower()):
        die("bond_txid gecersiz")
    txid = txid.lower()
    if manifest.get("amount_zat") != 1_000_000 or isinstance(manifest.get("amount_zat"), bool):
        die("amount invariant eslesmiyor")
    if manifest.get("memo") != f"HB1:{public_id}":
        die("memo invariant eslesmiyor")
    capture_confirmations = manifest.get("confirmations_at_capture")
    if not isinstance(capture_confirmations, int) or isinstance(capture_confirmations, bool) or capture_confirmations < 1:
        die("manifest capture confirmation gecersiz")

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT public_id,invoice_address,memo,amount_zat,status,bond_txid,bond_pool,bond_output_index,"
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
        or not isinstance(row["invoice_address"], str)
        or row["bond_txid"] != txid
        or row["bond_pool"] != manifest.get("bond_pool")
        or row["bond_output_index"] != manifest.get("bond_output_index")
        or row["bond_mined_height"] != manifest.get("mined_height")
    ):
        die("DB receipt manifest ile eslesmiyor")
    if row["status"] != "moderation" or row["moderation_decision"] is not None or row["refund_txid"] is not None:
        die(f"fixture tek kullanim icin hazir degil (status={row['status']})")
    if not isinstance(row["bond_confirmations"], int) or row["bond_confirmations"] < 1:
        die("DB confirmation kaydi eksik")

    try:
        evidence = collect_wallet_evidence(
            txid,
            receiver=row["invoice_address"],
            amount_zat=1_000_000,
            memo=manifest["memo"],
            pool=manifest.get("bond_pool"),
            output_index=manifest.get("bond_output_index"),
            mined_height=manifest.get("mined_height"),
        )
    except WalletEvidenceError as exc:
        die(str(exc))
    captured_blockhash = manifest.get("bond_blockhash")
    if captured_blockhash is not None and (
        not isinstance(captured_blockhash, str)
        or not TXID_RE.fullmatch(captured_blockhash.lower())
        or captured_blockhash.lower() != evidence["blockhash"]
    ):
        die("wallet mined blockhash capture ile eslesmiyor")
    if evidence["confirmations"] < capture_confirmations:
        die("wallet confirmation capture seviyesinin altina dustu")
    print(
        f"[hushboard] stage fixture OK: #{public_id} tx={txid[:10]}…{txid[-8:]} "
        f"height={evidence['mined_height']} confirmations={evidence['confirmations']} "
        "exact wallet receiver/amount/memo proven"
    )


if __name__ == "__main__":
    main()
