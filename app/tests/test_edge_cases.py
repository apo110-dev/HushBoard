from __future__ import annotations

from app.config import Settings
from app.service import HushBoardService
from app.wallet import DerivedAddress, OperationLaunch


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


def tx(txid, address, *, value=1_000_000, memo=None, index=0, pool="ironwood", height=99):
    return {
        "txid": txid,
        "mined_height": height,
        "outputs": [{
            "pool": pool,
            "output_index": index,
            "to_address": address,
            "value": value,
            "memo": memo,
            "is_change": False,
        }],
    }


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
