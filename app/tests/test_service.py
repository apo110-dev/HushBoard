from __future__ import annotations

import base64
import json
import stat
from dataclasses import replace

import pytest

from app.database import StateConflict
from app.service import HushBoardService, InputRejected, utc_now
from app.wallet import WalletUnavailable


class _UnavailableWallet:
    def probe(self, _role: str):
        raise WalletUnavailable("wallet RPC is unavailable")

    def identity(self, _role: str):
        raise WalletUnavailable("wallet RPC is unavailable")

    def validate_refund_address(self, _address: str):
        raise WalletUnavailable("wallet RPC is unavailable")


def test_auto_mode_wallet_outage_is_live_and_fail_closed(settings, refund_address):
    auto_settings = replace(settings, requested_mode="auto")
    service = HushBoardService(auto_settings, wallet=_UnavailableWallet())

    health = service.health()
    assert health["mode"] == "live"
    assert health["ok"] is False
    assert "fail-closed" in health["mode_label"]
    with pytest.raises(InputRejected, match="wallet service is unavailable"):
        service.create_submission("Must fail", "Never create synthetic success.", refund_address)
    assert service.list_submissions(status=None, query=None, limit=10, offset=0)["total"] == 0

def test_database_and_sqlite_sidecars_are_owner_only(settings, refund_address):
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    settings.database_path.touch(mode=0o644)
    settings.database_path.chmod(0o644)

    service = HushBoardService(settings)
    service.create_submission("Private DB", "Full refund addresses stay owner-only.", refund_address)

    for suffix in ("", "-wal", "-shm"):
        candidate = settings.database_path.with_name(settings.database_path.name + suffix)
        if candidate.exists():
            assert stat.S_IMODE(candidate.stat().st_mode) == 0o600

def test_full_mock_refund_flow(settings, refund_address):
    service = HushBoardService(settings)
    created = service.create_submission("Clear search labels", "Please make filters easier to scan.", refund_address)

    assert len(created["id"]) == 12
    assert created["status"] == "awaiting_bond"
    assert created["demo"] is True
    assert created["invoice"]["amount_zat"] == 1_000_000
    assert created["invoice"]["amount_zec"] == "0.01000000"
    assert created["invoice"]["receiver_types"] == ["orchard"]
    assert created["invoice"]["memo"] == f"HB1:{created['id']}"
    encoded_memo = created["invoice"]["uri"].split("memo=", 1)[1]
    padded = encoded_memo + "=" * (-len(encoded_memo) % 4)
    assert base64.urlsafe_b64decode(padded).decode() == created["invoice"]["memo"]
    assert refund_address not in json.dumps(created)

    sent = service.demo_send(created["id"])
    assert sent["submission"]["status"] == "bond_pending"
    assert sent["operation"]["status"] == "success"
    assert sent["operation"]["explorer_url"] is None

    synced = service.sync()
    assert synced["matched"] >= 1
    assert service.get_submission(created["id"])["status"] == "moderation"

    moderated = service.moderate(created["id"], decision="refund", note="Constructive")
    assert moderated["submission"]["status"] == "refund_broadcast"
    assert moderated["operation"]["txid"]

    service.sync()
    final = service.get_submission(created["id"])
    assert final["status"] == "refunded"
    assert final["refund"]["confirmations"] == 1
    assert [event["to_status"] for event in final["timeline"]] == [
        "awaiting_bond", "bond_pending", "moderation", "refund_broadcast", "refunded"
    ]


def test_keep_is_terminal_and_state_machine_rejects_replay(settings, refund_address):
    service = HushBoardService(settings)
    created = service.create_submission("Keyboard nav", "Add a visible focus ring.", refund_address)
    service.demo_send(created["id"])
    service.sync()
    kept = service.moderate(created["id"], decision="keep", note=None)
    assert kept["submission"]["status"] == "kept"

    with pytest.raises(StateConflict):
        service.db.transition(
            created["id"], "refunded", reason="illegal", now=utc_now()
        )


def test_seed_contains_honestly_labeled_demo_rows(settings):
    service = HushBoardService(settings)
    result = service.seed(reset=True, count=8)
    assert result["count"] == 8
    assert all(item["demo"] and item["mode"] == "mock" for item in result["created"])
    statuses = {item["status"] for item in result["created"]}
    assert "mismatch" in statuses
    assert "failure" in statuses


class _LiveRefundWallet:
    def __init__(
        self,
        invoice_address: str,
        txid: str,
        *,
        broadcast: bool = True,
        operation_error: str | None = None,
    ):
        from app.wallet import DerivedAddress
        self.derived = DerivedAddress(invoice_address, "42", ("orchard",))
        self.txid = txid
        self.broadcast = broadcast
        self.operation_error = operation_error
        self.operation_id = "opid-00000000-0000-0000-0000-000000000099"

    def validate_refund_address(self, address):
        return None

    def derive_invoice_address(self):
        return self.derived

    def send_refund(self, address, memo):
        from app.wallet import OperationLaunch
        return OperationLaunch(self.operation_id, "operator")

    def operation_statuses(self, role, operation_ids):
        if self.operation_error is not None:
            return [{
                "id": self.operation_id,
                "status": "failed",
                "error": {"code": -5, "message": self.operation_error},
            }]
        return [{
            "id": self.operation_id,
            "status": "success",
            "result": {"txid": self.txid, "txids": [self.txid], "broadcast": self.broadcast},
        }]

    def list_operator_transactions(self):
        return []

    def view_transaction(self, role, txid):
        return {"txid": txid, "status": "waiting", "confirmations": 0}


def test_live_refund_enters_broadcast_only_after_operation_evidence(tmp_path, refund_address):
    from app.config import Settings
    from app.service import utc_now

    invoice = "utest1" + "p" * 104
    refund_txid = "ab" * 32
    wallet = _LiveRefundWallet(invoice, refund_txid)
    live = Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / "live.db"),
        "ADMIN_KEY": "unit-test-live-admin",
        "HUSHBOARD_ENABLE_LIVE_SENDS": "1",
    }, root=tmp_path)
    service = HushBoardService(live, wallet=wallet)
    created = service.create_submission("Evidence rule", "Do not claim broadcast early.", refund_address)
    now = utc_now()
    service.db.transition(created["id"], "bond_pending", reason="test_seen", now=now)
    service.db.transition(created["id"], "moderation", reason="test_confirmed", now=now)

    queued = service.moderate(created["id"], decision="refund", note="approved")
    assert queued["submission"]["status"] == "moderation"
    assert queued["submission"]["moderation"]["decision"] == "refund"
    assert queued["submission"]["refund"]["txid"] is None
    assert queued["submission"]["can_moderate"] is False
    from app.service import InvalidAction
    with pytest.raises(InvalidAction):
        service.moderate(created["id"], decision="refund", note="duplicate")

    counters = {"warnings": [], "operations_updated": 0, "submissions_updated": 0}
    service._poll_operations(counters)
    evidenced = service.get_submission(created["id"])
    assert evidenced["status"] == "refund_broadcast"
    assert evidenced["refund"]["txid"] == refund_txid
    assert [event["to_status"] for event in evidenced["timeline"]][-1] == "refund_broadcast"



def test_live_refund_broadcast_false_fails_closed(tmp_path, refund_address):
    from app.config import Settings
    from app.service import utc_now

    wallet = _LiveRefundWallet("utest1" + "p" * 104, "cd" * 32, broadcast=False)
    live = Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / "not-broadcast.db"),
        "ADMIN_KEY": "unit-test-live-admin",
        "HUSHBOARD_ENABLE_LIVE_SENDS": "1",
    }, root=tmp_path)
    service = HushBoardService(live, wallet=wallet)
    created = service.create_submission("Fail closed", "Broadcast false is not broadcast.", refund_address)
    now = utc_now()
    service.db.transition(created["id"], "bond_pending", reason="test_seen", now=now)
    service.db.transition(created["id"], "moderation", reason="test_confirmed", now=now)
    service.moderate(created["id"], decision="refund", note=None)

    counters = {"warnings": [], "operations_updated": 0, "submissions_updated": 0}
    service._poll_operations(counters)
    result = service.get_submission(created["id"])
    assert result["status"] == "failure"
    assert result["refund"]["txid"] is None
    assert "broadcast" in result["refund"]["error"]

def test_async_operation_error_never_persists_or_returns_full_refund_ua(tmp_path, refund_address):
    from app.config import Settings

    wallet = _LiveRefundWallet(
        "utest1" + "p" * 104,
        "ef" * 32,
        operation_error=f"Invalid refund recipient: {refund_address}",
    )
    live = Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / "redacted-operation.db"),
        "ADMIN_KEY": "unit-test-live-admin",
        "HUSHBOARD_ENABLE_LIVE_SENDS": "1",
    }, root=tmp_path)
    service = HushBoardService(live, wallet=wallet)
    created = service.create_submission("Redact async", "Do not persist a full UA.", refund_address)
    now = utc_now()
    service.db.transition(created["id"], "bond_pending", reason="test_seen", now=now)
    service.db.transition(created["id"], "moderation", reason="test_confirmed", now=now)
    service.moderate(created["id"], decision="refund", note=None)

    counters = {"warnings": [], "operations_updated": 0, "submissions_updated": 0}
    service._poll_operations(counters)
    serialized = json.dumps(service.get_submission(created["id"]))
    assert refund_address not in serialized
    assert "Invalid refund recipient" not in serialized
    assert "wallet operation failed (code -5)" in serialized
    with service.db.connection() as conn:
        stored = conn.execute("SELECT error_message FROM operations").fetchone()[0]
    assert stored == "wallet operation failed (code -5)"
