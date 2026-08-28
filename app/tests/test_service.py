from __future__ import annotations

import base64
import json
import sqlite3
import stat
from dataclasses import replace

import pytest

from app.database import SCHEMA, Database, DatabaseError, StateConflict
from app.service import HushBoardService, InputRejected, InvalidAction, utc_now
from app.wallet import WalletUnavailable

TEST_ACCOUNT_UUID = "12345678-1234-" + "4abc-8def-123456789abc"


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

def test_v1_database_migrates_send_reservation_key_without_losing_operations(tmp_path):
    path = tmp_path / "legacy-v1.db"
    legacy_schema = SCHEMA.replace("    request_key TEXT,\n", "", 1)
    conn = sqlite3.connect(path)
    conn.executescript(legacy_schema)
    conn.execute("PRAGMA user_version=1")
    now = "2026-01-01T00:00:00Z"
    cursor = conn.execute(
        "INSERT INTO submissions "
        "(public_id,title,body,refund_address,refund_address_hint,invoice_address,zip321_uri,"
        "memo,amount_zat,status,demo,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "abcdefghijkl", "legacy", "legacy row", "utest1" + "q" * 104,
            "utest1…qqqqqqqq", "utest1" + "p" * 104, "zcash:legacy",
            "HB1:abcdefghijkl", 1_000_000, "awaiting_bond", 0, now, now,
        ),
    )
    conn.execute(
        "INSERT INTO operations "
        "(operation_id,submission_id,kind,wallet_role,status,created_at,updated_at) "
        "VALUES (?,?, 'bond_send','participant','queued',?,?)",
        ("opid-legacy", cursor.lastrowid, now, now),
    )
    conn.commit()
    conn.close()

    database = Database(path)
    database.initialize()
    with database.connection() as migrated:
        columns = {
            row["name"] for row in migrated.execute("PRAGMA table_info(operations)")
        }
        operation = migrated.execute(
            "SELECT operation_id,request_key FROM operations"
        ).fetchone()
        indexes = {
            row["name"] for row in migrated.execute("PRAGMA index_list(operations)")
        }
        version = migrated.execute("PRAGMA user_version").fetchone()[0]
    assert "request_key" in columns
    assert "operations_request_key_uq" in indexes
    assert operation["operation_id"] == "opid-legacy"
    assert operation["request_key"] is None
    assert version == 2


def test_database_rejects_future_schema_version_without_downgrading(tmp_path):
    path = tmp_path / "future.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()

    with pytest.raises(DatabaseError, match="newer than supported"):
        Database(path).initialize()

    conn = sqlite3.connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 99


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
        self.refund_send_calls = 0
        self.transactions = []
        self.transaction_details = {}

    def validate_refund_address(self, address):
        return None

    def derive_invoice_address(self):
        return self.derived

    def send_refund(self, address, memo):
        from app.wallet import OperationLaunch
        self.refund_send_calls += 1
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
        return self.transactions

    def view_transaction(self, role, txid):
        return self.transaction_details.get(
            txid, {"txid": txid, "status": "waiting", "confirmations": 0}
        )


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
    duplicate = service.moderate(created["id"], decision="refund", note="duplicate")
    assert duplicate["operation"]["id"] == queued["operation"]["id"]
    assert wallet.refund_send_calls == 1

    counters = {"warnings": [], "operations_updated": 0, "submissions_updated": 0}
    service._poll_operations(counters)
    evidenced = service.get_submission(created["id"])
    assert evidenced["status"] == "refund_broadcast"
    assert evidenced["refund"]["txid"] == refund_txid
    assert [event["to_status"] for event in evidenced["timeline"]][-1] == "refund_broadcast"



def test_refund_attach_failure_keeps_durable_intent_and_never_resends(
    tmp_path, refund_address
):
    from app.config import Settings
    from app.service import utc_now

    wallet = _LiveRefundWallet("utest1" + "p" * 104, "bc" * 32)
    live = Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / "refund-attach-failure.db"),
        "ADMIN_KEY": "unit-test-live-admin",
        "HUSHBOARD_ENABLE_LIVE_SENDS": "1",
    }, root=tmp_path)
    service = HushBoardService(live, wallet=wallet)
    created = service.create_submission(
        "Durable intent", "The RPC side effect must never precede durable intent.", refund_address
    )
    now = utc_now()
    service.db.transition(created["id"], "bond_pending", reason="test_seen", now=now)
    service.db.transition(created["id"], "moderation", reason="test_confirmed", now=now)
    with service.db.connection() as conn:
        conn.execute(
            "CREATE TRIGGER reject_operation_attach BEFORE UPDATE OF operation_id ON operations "
            "BEGIN SELECT RAISE(ABORT,'simulated attach failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated attach failure"):
        service.moderate(created["id"], decision="refund", note="approved")

    stranded = service.db.get_submission(created["id"])
    operations = service.db.operations_for_submission(stranded["id"])
    assert wallet.refund_send_calls == 1
    assert stranded["status"] == "moderation"
    assert stranded["moderation_decision"] == "refund"
    assert stranded["refund_operation_id"].startswith("intent-")
    assert len(operations) == 1
    assert operations[0]["operation_id"] == stranded["refund_operation_id"]
    assert operations[0]["status"] == "executing"
    assert operations[0]["request_key"] == f"refund:{stranded['id']}"
    pending_public = service.get_submission(created["id"])
    assert pending_public["refund"]["operation_id"] is None
    assert pending_public["refund"]["operation_status"] == "executing"

    with pytest.raises(InvalidAction, match="already in progress"):
        service.moderate(created["id"], decision="refund", note="duplicate")
    assert wallet.refund_send_calls == 1

    # A restarted process sees the same durable intent and cannot issue a replacement.
    restarted = HushBoardService(live, wallet=wallet)
    with pytest.raises(InvalidAction, match="already in progress"):
        restarted.moderate(created["id"], decision="refund", note="restart duplicate")
    assert wallet.refund_send_calls == 1

    # Once the short in-flight grace has elapsed, sync turns the orphan into a manual,
    # non-retryable failure instead of issuing a replacement refund.
    with service.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE operations SET created_at='2000-01-01T00:00:00Z' "
            "WHERE operation_id=?",
            (stranded["refund_operation_id"],),
        )
    reconciliation = {
        "warnings": [], "operations_updated": 0, "submissions_updated": 0
    }
    restarted._reconcile_launch_intents(reconciliation)
    reconciled = restarted.get_submission(created["id"])
    final_operation = restarted.db.operations_for_submission(stranded["id"])[0]
    assert reconciliation["operations_updated"] == 1
    assert reconciliation["submissions_updated"] == 1
    assert reconciled["status"] == "failure"
    assert "manual reconciliation" in reconciled["refund"]["error"]
    assert final_operation["status"] == "failed"
    retry = restarted.moderate(created["id"], decision="refund", note="retry")
    assert retry["operation"]["id"] == stranded["refund_operation_id"]
    assert wallet.refund_send_calls == 1

    # An exact-looking incoming note is not proof that the operator sent a refund.
    spoof_txid = "ad" * 32
    wallet.transactions = [
        {
            "account_uuid": TEST_ACCOUNT_UUID,
            "txid": spoof_txid,
            "mined_height": 122,
            "sent_note_count": 1,
            "received_note_count": 1,
            "expired_unmined": False,
            "outputs": [
                {
                    "pool": "orchard",
                    "output_index": 0,
                    "from_account": None,
                    "to_account": TEST_ACCOUNT_UUID,
                    "to_address": refund_address,
                    "value": created["invoice"]["amount_zat"],
                    "memo": created["invoice"]["memo"],
                    "is_change": False,
                }
            ],
        }
    ]
    wallet.transaction_details[spoof_txid] = {
        "txid": spoof_txid,
        "status": "mined",
        "confirmations": 99,
    }
    restarted.sync()
    assert restarted.get_submission(created["id"])["status"] == "failure"
    assert restarted.db.operations_for_submission(stranded["id"])[0]["status"] == "failed"
    assert wallet.refund_send_calls == 1

    # Exact mined outgoing wallet history is stronger than the earlier ambiguity.
    # It repairs the same stable intent and advances the refund without another send.
    late_txid = "de" * 32
    wallet.transactions = [
        {
            "account_uuid": TEST_ACCOUNT_UUID,
            "txid": late_txid,
            "mined_height": 123,
            "sent_note_count": 1,
            "received_note_count": 0,
            "expired_unmined": False,
            "outputs": [
                {
                    "pool": "orchard",
                    "output_index": 0,
                    "from_account": TEST_ACCOUNT_UUID,
                    "to_account": None,
                    "to_address": refund_address,
                    "value": created["invoice"]["amount_zat"],
                    "memo": created["invoice"]["memo"],
                    "is_change": False,
                }
            ],
        }
    ]
    wallet.transaction_details[late_txid] = {
        "txid": late_txid,
        "status": "mined",
        "confirmations": 99,
    }
    sync_result = restarted.sync()
    repaired = restarted.get_submission(created["id"])
    repaired_operation = restarted.db.operations_for_submission(stranded["id"])[0]
    assert sync_result["operations_updated"] == 1
    assert repaired["status"] == "refunded"
    assert repaired["refund"]["txid"] == late_txid
    assert repaired_operation["operation_id"] == stranded["refund_operation_id"]
    assert repaired_operation["status"] == "success"
    assert repaired_operation["txid"] == late_txid
    assert repaired_operation["broadcast"] == 1
    assert wallet.refund_send_calls == 1


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
