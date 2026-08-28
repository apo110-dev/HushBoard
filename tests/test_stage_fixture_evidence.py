from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "hushboard_stage_fixture_verifier", ROOT / "scripts" / "verify-stage-fixture.py"
)
assert VERIFY_SPEC and VERIFY_SPEC.loader
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)
CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "hushboard_stage_fixture_capture", ROOT / "scripts" / "capture-stage-fixture.py"
)
assert CAPTURE_SPEC and CAPTURE_SPEC.loader
CAPTURE = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(CAPTURE)

TXID = "1" * 64
BLOCKHASH = "2" * 64
RECEIVER = "utest1" + "q" * 104
PUBLIC_ID = "abcdefghjkmn"
MEMO = f"HB1:{PUBLIC_ID}"
ACCOUNT_UUID = str(uuid.UUID("12345678-1234-" + "4abc-8def-" + "123456789abc"))
MINED_HEIGHT = 4_280_072


def _memo_hex(text: str) -> str:
    encoded = text.encode("utf-8")
    return (encoded + b"\x00" * (512 - len(encoded))).hex()


def complete_view() -> dict[str, object]:
    return {
        "txid": TXID,
        "status": "mined",
        "confirmations": 7,
        "blockhash": BLOCKHASH,
        "blockindex": 3,
        "blocktime": 1_787_940_000,
        "outputs": [
            {
                "pool": "ironwood",
                "action": 0,
                "account_uuid": ACCOUNT_UUID,
                "address": RECEIVER,
                "outgoing": False,
                "walletInternal": False,
                "value": 0.01000000,
                "valueZat": 1_000_000,
                "memo": _memo_hex(MEMO),
                "memoStr": MEMO,
            }
        ],
    }


def complete_history() -> list[dict[str, object]]:
    return [
        {
            "account_uuid": ACCOUNT_UUID,
            "mined_height": MINED_HEIGHT,
            "txid": TXID,
            "received_note_count": 1,
            "expired_unmined": False,
            "outputs": [
                {
                    "pool": "ironwood",
                    "output_index": 0,
                    "from_account": None,
                    "to_account": ACCOUNT_UUID,
                    "to_address": RECEIVER,
                    "value": 1_000_000,
                    "is_change": False,
                    "memo": MEMO,
                }
            ],
        }
    ]


class FakeRpcRunner:
    def __init__(self, view: object, history: object):
        self.view = view
        self.history = history
        self.calls: list[tuple[str, object]] = []

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        method = command[2]
        params = json.loads(command[3])
        self.calls.append((method, params))
        result = self.view if method == "z_viewtransaction" else self.history
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}),
            "",
        )


def verify_with(runner: FakeRpcRunner) -> dict[str, object]:
    return VERIFY.collect_wallet_evidence(
        TXID,
        receiver=RECEIVER,
        amount_zat=1_000_000,
        memo=MEMO,
        pool="ironwood",
        output_index=0,
        mined_height=MINED_HEIGHT,
        runner=runner,
    )


def test_status_only_wallet_response_fails_without_decrypted_output_evidence() -> None:
    insufficient = complete_view()
    insufficient.pop("outputs")
    runner = FakeRpcRunner(insufficient, complete_history())

    with pytest.raises(VERIFY.WalletEvidenceError, match="wallet outputs eksik"):
        verify_with(runner)

    # A manifest/DB can repeat status, amount and memo, but the verifier must stop
    # before trusting them when the wallet did not return the decrypted output.
    assert [method for method, _ in runner.calls] == ["z_viewtransaction"]


def test_complete_zallet_beta_receipts_prove_exact_wallet_output_and_height() -> None:
    runner = FakeRpcRunner(complete_view(), complete_history())

    evidence = verify_with(runner)

    assert evidence == {
        "confirmations": 7,
        "mined_height": MINED_HEIGHT,
        "blockhash": BLOCKHASH,
        "pool": "ironwood",
        "output_index": 0,
    }
    assert runner.calls == [
        ("z_viewtransaction", [TXID]),
        ("z_listtransactions", [None, None, None, 0, 500]),
    ]


@pytest.mark.parametrize("bad_value", [True, 1_000_000.0, "1000000"])
def test_listed_zatoshi_amount_must_be_a_real_json_integer(bad_value: object) -> None:
    history = complete_history()
    history[0]["outputs"][0]["value"] = bad_value  # type: ignore[index]

    with pytest.raises(VERIFY.WalletEvidenceError, match="amount integer gecersiz"):
        verify_with(FakeRpcRunner(complete_view(), history))


def test_decimal_zec_conversion_is_exact_and_never_uses_binary_float() -> None:
    assert VERIFY._zec_value_to_zat(Decimal("0.01000000")) == 1_000_000
    with pytest.raises(VERIFY.WalletEvidenceError, match="tam zatoshi degil"):
        VERIFY._zec_value_to_zat(Decimal("0.010000001"))
    with pytest.raises(VERIFY.WalletEvidenceError, match="tipi gecersiz"):
        VERIFY._zec_value_to_zat(0.01)


def test_view_and_history_amount_fields_must_not_contradict() -> None:
    view = copy.deepcopy(complete_view())
    view["outputs"][0]["valueZat"] = 999_999  # type: ignore[index]

    with pytest.raises(VERIFY.WalletEvidenceError, match="amount celisiyor"):
        verify_with(FakeRpcRunner(view, complete_history()))


def test_capture_writes_only_wallet_proven_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "stage.db"
    output = tmp_path / "stage-fixture.json"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE submissions ("
            "public_id TEXT, title TEXT, invoice_address TEXT, memo TEXT, amount_zat INTEGER, "
            "status TEXT, bond_txid TEXT, bond_pool TEXT, bond_output_index INTEGER, "
            "bond_confirmations INTEGER, bond_mined_height INTEGER, moderation_decision TEXT, "
            "refund_txid TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                PUBLIC_ID,
                "Stage case",
                RECEIVER,
                MEMO,
                1_000_000,
                "moderation",
                TXID,
                "ironwood",
                0,
                1,
                MINED_HEIGHT,
                None,
                None,
                "2026-08-17T15:06:01Z",
            ),
        )

    seen: dict[str, object] = {}

    def proven(txid: str, **expected: object) -> dict[str, object]:
        seen.update({"txid": txid, **expected})
        return {
            "confirmations": 7,
            "mined_height": MINED_HEIGHT,
            "blockhash": BLOCKHASH,
            "pool": "ironwood",
            "output_index": 0,
        }

    monkeypatch.setattr(CAPTURE, "collect_wallet_evidence", proven)
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture-stage-fixture.py", PUBLIC_ID, "--db", str(database), "--output", str(output)],
    )

    CAPTURE.main()

    manifest = json.loads(output.read_text())
    assert seen == {
        "txid": TXID,
        "receiver": RECEIVER,
        "amount_zat": 1_000_000,
        "memo": MEMO,
        "pool": "ironwood",
        "output_index": 0,
        "mined_height": MINED_HEIGHT,
    }
    assert manifest["bond_blockhash"] == BLOCKHASH
    assert manifest["confirmations_at_capture"] == 7
    assert "receiver" not in manifest
    assert output.stat().st_mode & 0o777 == 0o600


def test_supported_alias_and_wrapped_shapes_remain_strict_but_compatible() -> None:
    view = complete_view()
    view["outputs"] = [
        {
            "pool": "ironwood",
            "outputIndex": 0,
            "accountUuid": ACCOUNT_UUID,
            "to_address": RECEIVER,
            "outgoing": False,
            "wallet_internal": False,
            "value": "0.01000000",
            "value_zat": 1_000_000,
            "memo_str": MEMO,
        }
    ]
    history = {
        "transactions": [
            {
                "accountUuid": ACCOUNT_UUID,
                "minedHeight": MINED_HEIGHT,
                "txid": TXID,
                "receivedNoteCount": 1,
                "expired_unmined": False,
                "outputs": [
                    {
                        "pool": "ironwood",
                        "outputIndex": 0,
                        "toAccount": ACCOUNT_UUID,
                        "toAddress": RECEIVER,
                        "valueZat": 1_000_000,
                        "isChange": False,
                        "memo": MEMO,
                    }
                ],
            }
        ]
    }

    evidence = verify_with(FakeRpcRunner(view, history))

    assert evidence["mined_height"] == MINED_HEIGHT
    assert evidence["confirmations"] == 7


def test_verifier_rejects_coherent_manifest_and_db_when_wallet_proof_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "stage.db"
    manifest_path = tmp_path / "stage-fixture.json"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE submissions ("
            "public_id TEXT, invoice_address TEXT, memo TEXT, amount_zat INTEGER, status TEXT, "
            "bond_txid TEXT, bond_pool TEXT, bond_output_index INTEGER, bond_mined_height INTEGER, "
            "bond_confirmations INTEGER, moderation_decision TEXT, refund_txid TEXT)"
        )
        conn.execute(
            "INSERT INTO submissions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                PUBLIC_ID,
                RECEIVER,
                MEMO,
                1_000_000,
                "moderation",
                TXID,
                "ironwood",
                0,
                MINED_HEIGHT,
                7,
                None,
                None,
            ),
        )
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "hushboard-stage-fixture/v1",
                "network": "zcash-testnet",
                "one_shot": True,
                "public_id": PUBLIC_ID,
                "bond_txid": TXID,
                "amount_zat": 1_000_000,
                "memo": MEMO,
                "bond_pool": "ironwood",
                "bond_output_index": 0,
                "mined_height": MINED_HEIGHT,
                "confirmations_at_capture": 1,
            }
        )
    )
    insufficient = complete_view()
    insufficient.pop("outputs")
    runner = FakeRpcRunner(insufficient, complete_history())
    real_collect = VERIFY.collect_wallet_evidence

    def collect_with_fake_rpc(txid: str, **expected: object) -> dict[str, object]:
        return real_collect(txid, **expected, runner=runner)

    monkeypatch.setattr(VERIFY, "collect_wallet_evidence", collect_with_fake_rpc)
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify-stage-fixture.py", "--manifest", str(manifest_path), "--db", str(database)],
    )

    with pytest.raises(SystemExit) as stopped:
        VERIFY.main()

    assert stopped.value.code == 1
    assert "wallet outputs eksik" in capsys.readouterr().err
    assert [method for method, _ in runner.calls] == ["z_viewtransaction"]
