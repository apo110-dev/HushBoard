from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import Settings
from app.service import HushBoardService, InvalidAction
from app.wallet import DerivedAddress, OperationLaunch, WalletUnavailable

ACCOUNT_UUID = "12345678-1234-" + "4abc-8def-123456789abc"


def live_settings(tmp_path, name="edge.db"):
    return Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / name),
        "ADMIN_KEY": "edge-case-test-admin",
        "HUSHBOARD_ENABLE_LIVE_SENDS": "1",
        "HUSHBOARD_WATCH_INTERVAL": "0",
    }, root=tmp_path)


def counters():
    return {
        "warnings": [], "scanned_transactions": 0, "scanned_outputs": 0,
        "matched": 0, "mismatched": 0, "operations_updated": 0,
        "submissions_updated": 0,
    }


class ScanWallet:
    def __init__(self):
        self.address = "utest1" + "p" * 104
        self.transactions = []
        self.details = {}

    def validate_refund_address(self, address):
        return None

    def derive_invoice_address(self):
        return DerivedAddress(self.address, "7", ("orchard",))

    def list_operator_transactions(self):
        return self.transactions

    def view_transaction(self, role, txid):
        return self.details[txid]


class BlockingBondWallet(ScanWallet):
    def __init__(self):
        super().__init__()
        self.bond_send_calls = 0
        self.send_entered = threading.Event()
        self.release_send = threading.Event()
        self.operation_id = "opid-20000000-0000-0000-0000-000000000001"

    def send_bond(self, address, memo):
        self.bond_send_calls += 1
        self.send_entered.set()
        assert self.release_send.wait(timeout=5)
        return OperationLaunch(self.operation_id, "participant")


class AcceptedThenTimedOutBondWallet(ScanWallet):
    def __init__(self):
        super().__init__()
        self.bond_send_calls = 0

    def send_bond(self, address, memo):
        self.bond_send_calls += 1
        raise WalletUnavailable("simulated timeout after wallet acceptance")


class MissingAttachedBondWallet(ScanWallet):
    def __init__(self, *, broadcast_false=False):
        super().__init__()
        self.bond_send_calls = 0
        self.operation_id = "opid-30000000-0000-0000-0000-000000000001"
        self.broadcast_false = broadcast_false

    def send_bond(self, address, memo):
        self.bond_send_calls += 1
        return OperationLaunch(self.operation_id, "participant")

    def operation_statuses(self, role, operation_ids):
        assert role == "participant"
        assert operation_ids == [self.operation_id]
        if self.broadcast_false:
            return [
                {
                    "id": self.operation_id,
                    "status": "success",
                    "result": {"txid": "91" * 32, "broadcast": False},
                }
            ]
        return []


def test_live_bond_send_reservation_is_idempotent_while_rpc_is_in_flight(
    tmp_path, refund_address
):
    wallet = BlockingBondWallet()
    settings = live_settings(tmp_path, "bond-idempotency.db")
    service = HushBoardService(settings, wallet=wallet)
    second_process = HushBoardService(settings, wallet=wallet)
    created = service.create_submission(
        "One launch", "Concurrent retries must share one durable send intent.", refund_address
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(service.demo_send, created["id"])
        assert wallet.send_entered.wait(timeout=5)
        with pytest.raises(InvalidAction, match="already in progress"):
            second_process.demo_send(created["id"])
        wallet.release_send.set()
        launched = first.result(timeout=5)

    repeated = service.demo_send(created["id"])
    operations = service.db.operations_for_submission(
        service.db.get_submission(created["id"])["id"]
    )
    assert wallet.bond_send_calls == 1
    assert len(operations) == 1
    assert operations[0]["request_key"] == f"bond_send:{operations[0]['submission_id']}"
    assert launched["operation"]["id"] == wallet.operation_id
    assert repeated["operation"]["id"] == wallet.operation_id
    assert service.get_submission(created["id"])["can_demo_send"] is False


def tx(txid, address, *, value=1_000_000, memo=None, index=0, pool="ironwood", height=99):
    return {
        "account_uuid": ACCOUNT_UUID,
        "txid": txid,
        "mined_height": height,
        "sent_note_count": 0,
        "received_note_count": 1,
        "expired_unmined": False,
        "outputs": [{
            "pool": pool,
            "output_index": index,
            "from_account": None,
            "to_account": ACCOUNT_UUID,
            "to_address": address,
            "value": value,
            "memo": memo,
            "is_change": False,
        }],
    }


def test_late_bond_evidence_repairs_failed_launch_intent_without_resending(
    tmp_path, refund_address
):
    wallet = AcceptedThenTimedOutBondWallet()
    service = HushBoardService(live_settings(tmp_path, "late-bond.db"), wallet=wallet)
    created = service.create_submission(
        "Late evidence", "A timed-out accepted send must reconcile from the chain.", refund_address
    )

    with pytest.raises(WalletUnavailable, match="wallet service is unavailable"):
        service.demo_send(created["id"])
    submission_id = service.db.get_submission(created["id"])["id"]
    failed_intent = service.db.operations_for_submission(submission_id)[0]
    assert failed_intent["operation_id"].startswith("intent-")
    assert failed_intent["status"] == "failed"
    assert wallet.bond_send_calls == 1

    txid = "ab" * 32
    wallet.transactions = [
        tx(txid, created["invoice"]["address"], memo=created["invoice"]["memo"])
    ]
    wallet.details[txid] = {"status": "mined", "confirmations": 2}
    result = service.sync()

    repaired = service.get_submission(created["id"])
    repaired_intent = service.db.operations_for_submission(submission_id)[0]
    assert result["operations_updated"] == 1
    assert repaired["status"] == "moderation"
    assert repaired["bond"]["txid"] == txid
    assert repaired_intent["operation_id"] == failed_intent["operation_id"]
    assert repaired_intent["status"] == "success"
    assert repaired_intent["txid"] == txid
    assert repaired_intent["broadcast"] == 1
    assert repaired_intent["error_message"] is None
    with pytest.raises(InvalidAction):
        service.demo_send(created["id"])
    assert wallet.bond_send_calls == 1


def test_mined_bond_repairs_attached_operation_lost_by_status_polling(
    tmp_path, refund_address
):
    wallet = MissingAttachedBondWallet()
    service = HushBoardService(live_settings(tmp_path, "missing-opid.db"), wallet=wallet)
    created = service.create_submission(
        "Lost opid", "A mined receipt must repair an ambiguously missing operation.", refund_address
    )
    launched = service.demo_send(created["id"])
    assert launched["operation"]["id"] == wallet.operation_id

    for _ in range(3):
        service._poll_operations(counters())
    submission_id = service.db.get_submission(created["id"])["id"]
    missing = service.db.operations_for_submission(submission_id)[0]
    assert missing["status"] == "failed"
    assert missing["missing_count"] == 3
    assert "no longer known" in missing["error_message"]

    txid = "92" * 32
    wallet.transactions = [
        tx(txid, wallet.address, memo=created["invoice"]["memo"], height=321)
    ]
    wallet.details[txid] = {"status": "mined", "confirmations": 5}
    result = service.sync()

    repaired = service.db.operations_for_submission(submission_id)[0]
    assert result["operations_updated"] == 1
    assert service.get_submission(created["id"])["status"] == "moderation"
    assert repaired["operation_id"] == wallet.operation_id
    assert repaired["status"] == "success"
    assert repaired["txid"] == txid
    assert repaired["broadcast"] == 1
    assert repaired["missing_count"] == 0
    assert repaired["error_message"] is None
    assert wallet.bond_send_calls == 1


def test_explicit_broadcast_false_operation_is_not_upgraded_by_late_bond(
    tmp_path, refund_address
):
    wallet = MissingAttachedBondWallet(broadcast_false=True)
    service = HushBoardService(live_settings(tmp_path, "negative-opid.db"), wallet=wallet)
    created = service.create_submission(
        "Definite negative", "Explicit broadcast false remains a failed operation.", refund_address
    )
    service.demo_send(created["id"])
    service._poll_operations(counters())

    txid = "91" * 32
    wallet.transactions = [
        tx(txid, wallet.address, memo=created["invoice"]["memo"], height=322)
    ]
    wallet.details[txid] = {"status": "mined", "confirmations": 5}
    service._scan_operator_transactions(counters())

    submission_id = service.db.get_submission(created["id"])["id"]
    operation = service.db.operations_for_submission(submission_id)[0]
    assert service.get_submission(created["id"])["bond"]["txid"] == txid
    assert operation["status"] == "failed"
    assert operation["broadcast"] == 0
    assert "broadcast was false" in operation["error_message"]
    assert wallet.bond_send_calls == 1


def test_wrong_amount_and_memo_are_quarantined_then_exact_output_can_bind(tmp_path, refund_address):
    wallet = ScanWallet()
    service = HushBoardService(live_settings(tmp_path), wallet=wallet)
    created = service.create_submission("Exactness", "Match integer amount and memo.", refund_address)
    memo = created["invoice"]["memo"]
    wrong_amount, wrong_memo, exact = "11" * 32, "22" * 32, "33" * 32
    wallet.transactions = [
        tx(wrong_amount, wallet.address, value=999_999, memo=memo),
        tx(wrong_memo, wallet.address, memo="HB1:not-the-invoice"),
    ]
    wallet.details = {
        wrong_amount: {"status": "mined", "confirmations": 2},
        wrong_memo: {"status": "mined", "confirmations": 2},
    }
    service._scan_operator_transactions(counters())
    mismatched = service.get_submission(created["id"])
    assert mismatched["status"] == "mismatch"
    assert mismatched["bond"]["txid"] is None

    wallet.transactions.append(tx(exact, wallet.address, memo=memo))
    wallet.details[exact] = {"status": "mined", "confirmations": 1}
    service._scan_operator_transactions(counters())
    bound = service.get_submission(created["id"])
    assert bound["status"] == "moderation"
    assert bound["bond"]["txid"] == exact
    outputs = service.db.outputs_for_submission(service.db.get_submission(created["id"])["id"])
    reasons = {row["mismatch_reason"] for row in outputs if row["mismatch_reason"]}
    assert any("wrong_amount" in reason for reason in reasons)
    assert any("memo_mismatch" in reason for reason in reasons)


def test_exact_looking_outgoing_note_cannot_bind_as_incoming_bond(
    tmp_path, refund_address
):
    wallet = ScanWallet()
    service = HushBoardService(live_settings(tmp_path, "direction.db"), wallet=wallet)
    created = service.create_submission(
        "Direction", "Only an operator-account receipt can satisfy the invoice.", refund_address
    )
    memo = created["invoice"]["memo"]
    spoof_txid = "34" * 32
    spoof = tx(spoof_txid, wallet.address, memo=memo)
    spoof["sent_note_count"] = 1
    spoof["outputs"][0]["from_account"] = ACCOUNT_UUID
    spoof["outputs"][0]["to_account"] = None
    wallet.transactions = [spoof]
    wallet.details[spoof_txid] = {"status": "mined", "confirmations": 10}

    service._scan_operator_transactions(counters())
    rejected = service.get_submission(created["id"])
    assert rejected["bond"]["txid"] is None
    assert rejected["status"] == "mismatch"

    valid_txid = "35" * 32
    wallet.transactions.append(tx(valid_txid, wallet.address, memo=memo))
    wallet.details[valid_txid] = {"status": "mined", "confirmations": 10}
    service._scan_operator_transactions(counters())
    assert service.get_submission(created["id"])["bond"]["txid"] == valid_txid


def test_bond_needs_mined_status_and_positive_non_bool_height_for_moderation(
    tmp_path, refund_address
):
    wallet = ScanWallet()
    service = HushBoardService(live_settings(tmp_path, "mined-proof.db"), wallet=wallet)
    created = service.create_submission(
        "Mined proof", "Confirmation counts alone must not unlock moderation.", refund_address
    )
    txid = "36" * 32
    transaction = tx(txid, wallet.address, memo=created["invoice"]["memo"])
    wallet.transactions = [transaction]
    wallet.details[txid] = {"status": "waiting", "confirmations": 99}

    service._scan_operator_transactions(counters())
    assert service.get_submission(created["id"])["status"] == "bond_pending"

    wallet.details[txid] = {"status": "mined", "confirmations": 99}
    transaction["mined_height"] = True
    service._scan_operator_transactions(counters())
    assert service.get_submission(created["id"])["status"] == "bond_pending"

    transaction["mined_height"] = 0
    service._scan_operator_transactions(counters())
    assert service.get_submission(created["id"])["status"] == "bond_pending"

    transaction["mined_height"] = 100
    service._scan_operator_transactions(counters())
    confirmed = service.get_submission(created["id"])
    assert confirmed["status"] == "moderation"
    assert confirmed["bond"]["tx_status"] == "mined"


def test_duplicate_exact_output_never_rebinds_first_bond(tmp_path, refund_address):
    wallet = ScanWallet()
    service = HushBoardService(live_settings(tmp_path, "duplicate.db"), wallet=wallet)
    created = service.create_submission("Duplicate", "Only one output is the bond.", refund_address)
    memo = created["invoice"]["memo"]
    first, second = "44" * 32, "55" * 32
    wallet.transactions = [
        tx(first, wallet.address, memo=memo),
        tx(second, wallet.address, memo=memo),
    ]
    wallet.details = {
        first: {"status": "mined", "confirmations": 2},
        second: {"status": "mined", "confirmations": 2},
    }
    service._scan_operator_transactions(counters())
    final = service.get_submission(created["id"])
    assert final["bond"]["txid"] == first
    outputs = service.db.outputs_for_submission(service.db.get_submission(created["id"])["id"])
    results = {row["txid"]: row["match_result"] for row in outputs}
    assert results[first] == "exact"
    assert results[second] == "duplicate_exact"


def test_confirmation_drop_before_moderation_decision_returns_to_pending(tmp_path, refund_address):
    wallet = ScanWallet()
    service = HushBoardService(live_settings(tmp_path, "reorg.db"), wallet=wallet)
    created = service.create_submission("Reorg", "Do not moderate a dropped confirmation.", refund_address)
    txid = "66" * 32
    wallet.transactions = [tx(txid, wallet.address, memo=created["invoice"]["memo"])]
    wallet.details[txid] = {"status": "mined", "confirmations": 1}
    service._scan_operator_transactions(counters())
    assert service.get_submission(created["id"])["status"] == "moderation"

    wallet.details[txid] = {"status": "waiting", "confirmations": -1}
    service._scan_operator_transactions(counters())
    dropped = service.get_submission(created["id"])
    assert dropped["status"] == "bond_pending"
    assert dropped["bond"]["confirmations"] == 0


class RestartWallet(ScanWallet):
    def __init__(self):
        super().__init__()
        self.operation_id = "opid-10000000-0000-0000-0000-000000000001"
        self.refund_txid = "77" * 32

    def send_refund(self, address, memo):
        return OperationLaunch(self.operation_id, "operator")

    def operation_statuses(self, role, operation_ids):
        assert operation_ids == [self.operation_id]
        return [{
            "id": self.operation_id,
            "status": "success",
            "result": {"txid": self.refund_txid, "txids": [self.refund_txid]},
        }]


def test_restart_reconciles_persisted_refund_operation_without_resend(tmp_path, refund_address):
    wallet = RestartWallet()
    settings = live_settings(tmp_path, "restart.db")
    first = HushBoardService(settings, wallet=wallet)
    created = first.create_submission("Restart", "Persist operation IDs before polling.", refund_address)
    now_tx = "88" * 32
    wallet.transactions = [tx(now_tx, wallet.address, memo=created["invoice"]["memo"])]
    wallet.details[now_tx] = {"status": "mined", "confirmations": 1}
    first._scan_operator_transactions(counters())
    queued = first.moderate(created["id"], decision="refund", note="approved")
    assert queued["submission"]["status"] == "moderation"
    assert queued["submission"]["refund"]["txid"] is None

    restarted = HushBoardService(settings, wallet=wallet)
    restarted._poll_operations(counters())
    reconciled = restarted.get_submission(created["id"])
    assert reconciled["status"] == "refund_broadcast"
    assert reconciled["refund"]["txid"] == wallet.refund_txid
    assert len([op for op in restarted.db.operations_for_submission(restarted.db.get_submission(created["id"])["id"])
                if op["kind"] == "refund"]) == 1


class HealthWallet:
    def probe(self, role):
        return {
            "node_tip": {"height": 100},
            "wallet_tip": {"height": 100 if role == "operator" else 99},
        }


def test_health_fails_closed_when_either_wallet_is_stale(tmp_path):
    service = HushBoardService(live_settings(tmp_path, "health.db"), wallet=HealthWallet())
    health = service.health()
    assert health["mode"] == "live"
    assert health["wallet"]["operator_connected"] is True
    assert health["wallet"]["participant_connected"] is True
    assert health["wallet"]["synced"] is False
    assert health["ok"] is False
