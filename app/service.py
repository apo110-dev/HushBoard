"""Application service: invoices, matching, moderation, and durable sync."""
from __future__ import annotations

import base64
import json
import re
import secrets
import sqlite3
import threading
from datetime import UTC, datetime
from functools import lru_cache
from io import BytesIO
from typing import Any
from uuid import UUID

from .config import Settings
from .database import STATUSES, Database, NotFound, StateConflict
from .wallet import (
    DerivedAddress,
    WalletError,
    WalletUnavailable,
    ZalletAdapter,
    extract_operation_result,
    mock_operation_id,
    mock_txid,
    mock_unified_address,
    public_wallet_error,
)

_PUBLIC_ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_PUBLIC_ID_RE = re.compile(r"^[a-z2-9]{12}$")
_TXID_RE = re.compile(r"^[0-9a-f]{64}$")
_INTENT_PREFIX = "intent-"
_LAUNCH_UNKNOWN_ERROR = "wallet launch outcome is unknown; manual reconciliation is required"
_OPERATION_MISSING_ERROR = (
    "operation is no longer known to Zallet; manual reconciliation is required"
)
_STATUS_LABELS = {
    "awaiting_bond": "Awaiting bond",
    "bond_pending": "Bond pending",
    "moderation": "In moderation",
    "refund_broadcast": "Refund broadcast",
    "refunded": "Refunded",
    "kept": "Bond kept",
    "mismatch": "Payment mismatch",
    "failure": "Needs attention",
}


class ServiceError(RuntimeError):
    code = "service_error"


class FeatureDisabled(ServiceError):
    code = "feature_disabled"


class InvalidAction(ServiceError):
    code = "invalid_state"


class InputRejected(ServiceError):
    code = "input_rejected"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_error(value: object, maximum: int = 240) -> str:
    if isinstance(value, WalletError):
        return public_wallet_error(value)
    text = " ".join(str(value).replace("\x00", "").split())
    text = re.sub(
        r"(?i)\butest1[023456789acdefghjklmnpqrstuvwxyz]{20,512}\b",
        "<redacted testnet UA>",
        text,
    )
    return text[:maximum] or "unknown error"


def _refund_hint(address: str) -> str:
    return f"{address[:13]}…{address[-8:]}"


def _public_id() -> str:
    return "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(12))


def _zip321(address: str, amount_zec: str, memo: str) -> str:
    memo64 = base64.urlsafe_b64encode(memo.encode("utf-8")).decode("ascii").rstrip("=")
    # address is Bech32 and memo64 is unpadded base64url: neither needs percent encoding.
    return f"zcash:{address}?amount={amount_zec}&memo={memo64}"


@lru_cache(maxsize=512)
def _qr_svg_data_url(payload: str) -> str | None:
    """Render a real QR as a self-contained data URL when the tiny QR dependency exists."""
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage
    except ImportError:
        return None
    image = qrcode.make(
        payload,
        image_factory=SvgPathImage,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=4,
    )
    output = BytesIO()
    image.save(output)
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class HushBoardService:
    def __init__(
        self,
        settings: Settings,
        *,
        database: Database | None = None,
        wallet: ZalletAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.db = database or Database(settings.database_path)
        self.db.initialize()
        self.wallet = wallet or ZalletAdapter(settings)
        self._mode: str | None = None
        self._mode_reason: str | None = None
        self._mode_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        # A committed intent exists before any wallet side effect. This in-memory set
        # only protects an intent that is still being launched by this process; after a
        # restart, sync can classify the durable orphan as an unknown outcome.
        self._launch_lock = threading.Lock()
        self._launching_intents: set[str] = set()
        self.watcher_running = False
        self.watcher_error: str | None = None
        self._offline_evidence_kinds: dict[str, str] | None = None

    @property
    def offline_snapshot(self) -> bool:
        return bool(self.db.get_meta("offline_snapshot"))

    def _reject_snapshot_mutation(self) -> None:
        if self.offline_snapshot:
            raise FeatureDisabled(
                "immutable offline replay is read-only; restart HushBoard to restore its disposable copy"
            )

    def _offline_evidence_kind(self, public_id: str) -> str | None:
        if not self.offline_snapshot:
            return None
        if self._offline_evidence_kinds is None:
            raw = self.db.get_meta("offline_evidence_kinds")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {}
            self._offline_evidence_kinds = {
                str(key): str(value)
                for key, value in parsed.items()
                if isinstance(key, str) and isinstance(value, str)
            } if isinstance(parsed, dict) else {}
        return self._offline_evidence_kinds.get(public_id)

    def resolve_mode(self) -> str:
        with self._mode_lock:
            if self._mode is not None:
                return self._mode
            requested = self.settings.requested_mode
            if requested == "mock":
                self._mode = "mock"
                if self.db.get_meta("offline_snapshot"):
                    self._mode_reason = "OFFLINE REPLAY · NO LIVE SENDS; checked-in snapshot copy."
                else:
                    self._mode_reason = (
                        "DEMO SIMULATION · NO LIVE SENDS; synthetic developer mode, not replay evidence."
                    )
            elif requested == "live":
                self._mode = "live"
                self._mode_reason = "Live Zcash testnet mode."
            else:
                # Auto chooses the real integration, never synthetic success. Wallet health
                # is evaluated on every health/action call and fails closed while unavailable.
                self._mode = "live"
                self._mode_reason = "Live Zcash testnet auto mode; wallet health is fail-closed."
            return self._mode

    @property
    def mode(self) -> str:
        return self.resolve_mode()

    @property
    def mode_label(self) -> str:
        self.resolve_mode()
        return self._mode_reason or ""

    def explorer_url(self, txid: str | None, *, demo: bool) -> str | None:
        if demo or not txid or not _TXID_RE.fullmatch(txid.lower()):
            return None
        template = self.settings.explorer_tx_template
        if "{txid}" in template:
            return template.replace("{txid}", txid.lower())
        return template.rstrip("/") + "/" + txid.lower()

    def health(self) -> dict[str, Any]:
        database_ok = self.db.health()
        mode = self.resolve_mode()
        wallets: dict[str, Any] = {
            "operator_connected": False,
            "participant_connected": False,
            "synced": False,
            "height": None,
        }
        wallet_warnings: list[str] = []
        if mode == "live":
            statuses: dict[str, dict[str, Any]] = {}
            for role in ("operator", "participant"):
                try:
                    statuses[role] = self.wallet.probe(role)
                    wallets[f"{role}_connected"] = True
                except WalletError as exc:
                    wallet_warnings.append(f"{role}: {_safe_error(exc)}")
            role_sync: list[bool] = []
            role_heights: list[int] = []
            for role in ("operator", "participant"):
                role_status = statuses.get(role, {})
                node_tip = role_status.get("node_tip") if isinstance(role_status, dict) else None
                wallet_tip = role_status.get("wallet_tip") if isinstance(role_status, dict) else None
                node_height = node_tip.get("height") if isinstance(node_tip, dict) else None
                wallet_height = wallet_tip.get("height") if isinstance(wallet_tip, dict) else None
                locked = role_status.get("locked") if isinstance(role_status, dict) else None
                role_sync.append(
                    bool(
                        wallets[f"{role}_connected"]
                        and isinstance(node_height, int)
                        and node_height == wallet_height
                        and locked is not True
                    )
                )
                if isinstance(wallet_height, int):
                    role_heights.append(wallet_height)
            wallets["height"] = role_heights[0] if role_heights and len(set(role_heights)) == 1 else None
            wallets["synced"] = len(role_sync) == 2 and all(role_sync) and len(set(role_heights)) == 1
        last_sync = self.db.get_meta("last_sync_at")
        snapshot: dict[str, Any] | None = None
        if mode == "mock":
            raw_snapshot = self.db.get_meta("offline_snapshot")
            if raw_snapshot:
                try:
                    candidate = json.loads(raw_snapshot)
                    if isinstance(candidate, dict):
                        snapshot = candidate
                except json.JSONDecodeError:
                    snapshot = None
            if snapshot and isinstance(snapshot.get("block_height"), int):
                wallets["height"] = snapshot["block_height"]
        return {
            "ok": bool(
                database_ok
                and (
                    mode == "mock"
                    or (
                        wallets["operator_connected"]
                        and wallets["participant_connected"]
                        and wallets["synced"]
                    )
                )
            ),
            "service": "HushBoard",
            "version": "1.0.0",
            "mode": mode,
            "mode_label": self.mode_label,
            "demo": mode == "mock",
            "snapshot": snapshot,
            "network": self.settings.network,
            "wallet": wallets,
            "wallet_warnings": wallet_warnings,
            "database": {"ok": database_ok},
            "watcher": {
                "running": self.watcher_running,
                "interval_seconds": self.settings.watcher_interval,
                "last_sync_at": last_sync,
                "last_error": self.watcher_error,
            },
            "bond": {
                "amount_zat": self.settings.bond_zat,
                "amount_zec": self.settings.bond_zec,
                "min_confirmations": self.settings.minimum_confirmations,
            },
            "admin_required": not (
                self.settings.demo_open_admin
                and (mode == "mock" or self.settings.admin_key == "local-demo-only")
            ),
            "live_sends_enabled": self.settings.live_sends_enabled,
            "explorer_base_url": self.settings.explorer_tx_template if mode == "live" else None,
        }

    def create_submission(self, title: str, body: str, refund_address: str) -> dict[str, Any]:
        self._reject_snapshot_mutation()
        mode = self.resolve_mode()
        if mode == "live":
            try:
                self.wallet.validate_refund_address(refund_address)
            except WalletError as exc:
                raise InputRejected(_safe_error(exc)) from exc

        last_error: Exception | None = None
        for _ in range(8):
            public_id = _public_id()
            memo = f"HB1:{public_id}"
            if mode == "mock":
                derived = DerivedAddress(mock_unified_address(), None, ("orchard",))
            else:
                try:
                    derived = self.wallet.derive_invoice_address()
                except WalletError as exc:
                    raise WalletUnavailable(_safe_error(exc)) from exc
            values = {
                "public_id": public_id,
                "title": title,
                "body": body,
                "refund_address": refund_address,
                "refund_address_hint": _refund_hint(refund_address),
                "invoice_address": derived.address,
                "invoice_diversifier_index": derived.diversifier_index,
                "zip321_uri": _zip321(derived.address, self.settings.bond_zec, memo),
                "memo": memo,
                "amount_zat": self.settings.bond_zat,
                "status": "awaiting_bond",
                "demo": int(mode == "mock"),
            }
            try:
                row = self.db.create_submission(values, now=utc_now())
            except sqlite3.IntegrityError as exc:
                # Public IDs and invoice addresses both have unique constraints. Never
                # reuse a memo/address after a collision.
                last_error = exc
                continue
            return self.serialize_submission(row, include_timeline=True)
        raise ServiceError("could not allocate a unique invoice") from last_error

    def get_submission(self, public_id: str) -> dict[str, Any]:
        self._validate_public_id(public_id)
        return self.serialize_submission(self.db.get_submission(public_id), include_timeline=True)

    def list_submissions(
        self,
        *,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        if status is not None and status not in STATUSES:
            raise InputRejected("unknown status filter")
        if query is not None:
            query = query.strip()
            if len(query) > 100:
                raise InputRejected("search query is too long")
            query = query or None
        rows, total = self.db.list_submissions(
            status=status, query=query, limit=limit, offset=offset
        )
        return {
            "items": [self.serialize_submission(row, include_timeline=False) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "mode": self.resolve_mode(),
        }

    @staticmethod
    def _validate_public_id(public_id: str) -> None:
        if not _PUBLIC_ID_RE.fullmatch(public_id):
            raise NotFound("submission not found")

    def serialize_submission(self, row: sqlite3.Row, *, include_timeline: bool) -> dict[str, Any]:
        demo = bool(row["demo"])
        outputs = self.db.outputs_for_submission(row["id"])
        operations = self.db.operations_for_submission(row["id"])
        bond_output = next(
            (
                out for out in outputs
                if row["bond_txid"]
                and out["txid"] == row["bond_txid"]
                and out["pool"] == row["bond_pool"]
                and out["output_index"] == row["bond_output_index"]
            ),
            None,
        )
        refund_operation = next(
            (op for op in reversed(operations) if op["kind"] == "refund"), None
        )
        bond_send_operation = next(
            (op for op in reversed(operations) if op["kind"] == "bond_send"), None
        )
        timeline: list[dict[str, Any]] = []
        if include_timeline:
            for event in self.db.timeline(row["id"]):
                metadata = None
                if event["metadata_json"]:
                    try:
                        metadata = json.loads(event["metadata_json"])
                    except json.JSONDecodeError:
                        metadata = None
                timeline.append(
                    {
                        "from_status": event["from_status"],
                        "to_status": event["to_status"],
                        "reason": event["reason"],
                        "metadata": metadata,
                        "at": event["created_at"],
                    }
                )
        qr_svg = _qr_svg_data_url(row["zip321_uri"])
        bond_explorer = self.explorer_url(row["bond_txid"], demo=demo)
        refund_explorer = self.explorer_url(row["refund_txid"], demo=demo)
        refund_operation_id = row["refund_operation_id"]
        if (
            isinstance(refund_operation_id, str)
            and self._is_launch_intent(refund_operation_id)
            and (
                refund_operation is None
                or refund_operation["status"] in {"queued", "executing"}
            )
        ):
            # This provisional primary key can be atomically replaced by Zallet's id.
            # Expose the pending status, but never a transient identifier.
            refund_operation_id = None
        return {
            "id": row["public_id"],
            "public_id": row["public_id"],
            "title": row["title"],
            "body": row["body"],
            "status": row["status"],
            "status_label": _STATUS_LABELS[row["status"]],
            "status_detail": row["status_detail"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "demo": demo,
            "mode": "mock" if demo else "live",
            "evidence_kind": self._offline_evidence_kind(row["public_id"]) if demo else None,
            "invoice": {
                "address": row["invoice_address"],
                "uri": row["zip321_uri"],
                "qr_payload": row["zip321_uri"],
                "qr_svg": qr_svg,
                "amount_zat": row["amount_zat"],
                "amount_zec": self.settings.bond_zec,
                "memo": row["memo"],
                "receiver_types": ["orchard"],
            },
            "refund_address_hint": row["refund_address_hint"],
            "bond": {
                "txid": row["bond_txid"],
                "pool": row["bond_pool"],
                "output_index": row["bond_output_index"],
                "confirmations": row["bond_confirmations"],
                "required_confirmations": self.settings.minimum_confirmations,
                "tx_status": row["bond_tx_status"],
                "received_amount_zat": bond_output["value_zat"] if bond_output else None,
                "received_memo": bond_output["memo"] if bond_output else None,
                "mismatch_reason": row["mismatch_reason"],
                "explorer_url": bond_explorer,
            },
            "refund": {
                "operation_id": refund_operation_id,
                "operation_status": refund_operation["status"] if refund_operation else None,
                "txid": row["refund_txid"],
                "confirmations": row["refund_confirmations"],
                "required_confirmations": self.settings.minimum_confirmations,
                "tx_status": row["refund_tx_status"],
                "error": row["refund_error"],
                "explorer_url": refund_explorer,
            },
            "moderation": {
                "decision": row["moderation_decision"],
                "note": row["moderation_note"],
                "decided_at": row["moderated_at"],
            },
            "timeline": timeline,
            # Flat aliases keep a lightweight SPA adapter simple.
            "bond_address": row["invoice_address"],
            "bond_uri": row["zip321_uri"],
            "bond_explorer_url": bond_explorer,
            "refund_explorer_url": refund_explorer,
            "can_demo_send": (
                not self.offline_snapshot
                and row["status"] in {"awaiting_bond", "mismatch", "failure"}
                and not row["bond_txid"]
                and bond_send_operation is None
            ),
            "can_moderate": (
                not self.offline_snapshot
                and row["status"] == "moderation"
                and row["moderation_decision"] is None
            ),
        }

    def _operation_public(self, operation: sqlite3.Row | dict[str, Any], *, demo: bool) -> dict[str, Any]:
        get = operation.__getitem__
        txid = get("txid")
        status = get("status")
        operation_id = get("operation_id")
        if self._is_launch_intent(operation_id) and status in {"queued", "executing"}:
            operation_id = None
        return {
            "id": operation_id,
            "kind": get("kind"),
            "status": status,
            "txid": txid,
            "broadcast": None if get("broadcast") is None else bool(get("broadcast")),
            "error": get("error_message"),
            "explorer_url": self.explorer_url(txid, demo=demo),
        }

    @staticmethod
    def _send_request_key(submission_id: int, kind: str) -> str:
        return f"{kind}:{submission_id}"

    @staticmethod
    def _is_launch_intent(operation_id: str) -> bool:
        return operation_id.startswith(_INTENT_PREFIX)

    @staticmethod
    def _canonical_account_uuid(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            canonical = str(UUID(value))
        except (ValueError, AttributeError):
            return None
        return canonical if value.lower() == canonical else None

    @staticmethod
    def _is_positive_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def _mined_evidence_txid(self, submission: sqlite3.Row, kind: str) -> str | None:
        if kind == "bond_send":
            txid = submission["bond_txid"]
            if (
                isinstance(txid, str)
                and submission["bond_tx_status"] == "mined"
                and self._is_positive_int(submission["bond_mined_height"])
            ):
                return txid
            return None
        txid = submission["refund_txid"]
        if isinstance(txid, str) and submission["refund_tx_status"] == "mined":
            return txid
        return None

    def _operation_accepts_late_evidence(self, operation: sqlite3.Row) -> bool:
        """Distinguish ambiguous loss from an explicit negative wallet result."""
        if operation["status"] in {"queued", "executing"}:
            return operation["broadcast"] != 0
        if operation["status"] != "failed" or operation["broadcast"] is not None:
            return False
        if operation["error_code"] is not None:
            return False
        if self._is_launch_intent(operation["operation_id"]):
            return operation["error_message"] == _LAUNCH_UNKNOWN_ERROR
        return (
            operation["missing_count"] >= 3
            and operation["error_message"] == _OPERATION_MISSING_ERROR
        )

    def _repair_operation_from_chain_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        submission_id: int,
        kind: str,
        txid: str,
        counters: dict[str, Any],
    ) -> bool:
        operations = conn.execute(
            "SELECT * FROM operations WHERE submission_id=? AND kind=? "
            "ORDER BY created_at DESC,operation_id DESC",
            (submission_id, kind),
        ).fetchall()
        for operation in operations:
            if operation["txid"] not in {None, txid}:
                continue
            if not self._operation_accepts_late_evidence(operation):
                continue
            conn.execute(
                "UPDATE operations SET status='success',txid=?,txids_json=?,broadcast=1,"
                "error_code=NULL,error_message=NULL,missing_count=0,updated_at=? "
                "WHERE operation_id=?",
                (txid, json.dumps([txid]), utc_now(), operation["operation_id"]),
            )
            counters["operations_updated"] += 1
            return True
        return False

    def _reserve_bond_launch(
        self, submission_id: int
    ) -> tuple[sqlite3.Row, sqlite3.Row, bool]:
        """Atomically reserve the one participant send allowed for an invoice."""
        intent_id = _INTENT_PREFIX + secrets.token_hex(16)
        request_key = self._send_request_key(submission_id, "bond_send")
        with self._launch_lock:
            self._launching_intents.add(intent_id)
        try:
            now = utc_now()
            with self.db.transaction(immediate=True) as conn:
                current = self.db.get_submission_by_id(submission_id, conn)
                if (
                    current["status"] not in {"awaiting_bond", "mismatch", "failure"}
                    or current["bond_txid"]
                ):
                    raise InvalidAction("this invoice cannot receive another demo bond")
                existing = conn.execute(
                    "SELECT * FROM operations WHERE submission_id=? AND kind='bond_send' "
                    "ORDER BY created_at,operation_id LIMIT 1",
                    (submission_id,),
                ).fetchone()
                if existing is not None:
                    with self._launch_lock:
                        self._launching_intents.discard(intent_id)
                    return current, existing, False
                conn.execute(
                    "INSERT INTO operations "
                    "(operation_id,request_key,submission_id,kind,wallet_role,status,created_at,updated_at) "
                    "VALUES (?,?,?,'bond_send','participant','executing',?,?)",
                    (intent_id, request_key, submission_id, now, now),
                )
                operation = conn.execute(
                    "SELECT * FROM operations WHERE operation_id=?", (intent_id,)
                ).fetchone()
                return current, operation, True
        except Exception:
            with self._launch_lock:
                self._launching_intents.discard(intent_id)
            raise

    def _reserve_refund_launch(
        self, submission_id: int, *, note: str | None
    ) -> tuple[sqlite3.Row, sqlite3.Row, bool]:
        """Persist a refund intent and moderator decision before calling Zallet."""
        intent_id = _INTENT_PREFIX + secrets.token_hex(16)
        request_key = self._send_request_key(submission_id, "refund")
        with self._launch_lock:
            self._launching_intents.add(intent_id)
        try:
            now = utc_now()
            with self.db.transaction(immediate=True) as conn:
                current = self.db.get_submission_by_id(submission_id, conn)
                existing = conn.execute(
                    "SELECT * FROM operations WHERE submission_id=? AND kind='refund' "
                    "ORDER BY created_at,operation_id LIMIT 1",
                    (submission_id,),
                ).fetchone()
                if existing is not None and current["moderation_decision"] == "refund":
                    with self._launch_lock:
                        self._launching_intents.discard(intent_id)
                    return current, existing, False
                if current["status"] != "moderation" or current["moderation_decision"] is not None:
                    raise InvalidAction("only an undecided, confirmed submission can be moderated")
                conn.execute(
                    "INSERT INTO operations "
                    "(operation_id,request_key,submission_id,kind,wallet_role,status,created_at,updated_at) "
                    "VALUES (?,?,?,'refund','operator','executing',?,?)",
                    (intent_id, request_key, submission_id, now, now),
                )
                reserved = self.db.transition_in_connection(
                    conn,
                    submission_id,
                    "moderation",
                    reason="refund_launch_reserved",
                    now=now,
                    updates={
                        "moderation_decision": "refund",
                        "moderation_note": note,
                        "moderated_at": now,
                        "refund_operation_id": intent_id,
                        "refund_error": None,
                        "status_detail": "Refund launch reserved durably; no broadcast evidence yet.",
                    },
                )
                operation = conn.execute(
                    "SELECT * FROM operations WHERE operation_id=?", (intent_id,)
                ).fetchone()
                return reserved, operation, True
        except Exception:
            with self._launch_lock:
                self._launching_intents.discard(intent_id)
            raise

    def _attach_wallet_operation(
        self,
        *,
        intent_id: str,
        submission_id: int,
        kind: str,
        launched: Any,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        """Replace a durable local intent with the RPC operation id atomically."""
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            intent = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?", (intent_id,)
            ).fetchone()
            if intent is None or intent["status"] != "executing":
                raise InvalidAction("wallet launch reservation is no longer active")
            cursor = conn.execute(
                "UPDATE operations SET operation_id=?,wallet_role=?,status='queued',"
                "missing_count=0,updated_at=? WHERE operation_id=? AND status='executing'",
                (launched.operation_id, launched.wallet_role, now, intent_id),
            )
            if cursor.rowcount != 1:
                raise InvalidAction("wallet launch reservation changed concurrently")
            submission = self.db.get_submission_by_id(submission_id, conn)
            if kind == "refund":
                submission = self.db.transition_in_connection(
                    conn,
                    submission_id,
                    "moderation",
                    reason="refund_operation_queued",
                    now=now,
                    updates={
                        "refund_operation_id": launched.operation_id,
                        "refund_error": None,
                        "status_detail": "Refund operation queued; awaiting broadcast evidence.",
                    },
                )
            operation = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?", (launched.operation_id,)
            ).fetchone()
            return submission, operation

    def _mark_launch_unknown(
        self,
        *,
        intent_id: str,
        submission_id: int,
        kind: str,
    ) -> None:
        """Record an ambiguous launch failure without ever making it retryable."""
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            operation = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?", (intent_id,)
            ).fetchone()
            if operation is None:
                return
            submission = self.db.get_submission_by_id(submission_id, conn)
            evidence_txid = self._mined_evidence_txid(submission, kind)
            if evidence_txid:
                # A watcher may have found chain evidence while the initiating request
                # was timing out. Evidence wins over the ambiguous RPC exception.
                conn.execute(
                    "UPDATE operations SET status='success',txid=?,txids_json=?,broadcast=1,"
                    "error_code=NULL,error_message=NULL,missing_count=0,updated_at=? "
                    "WHERE operation_id=?",
                    (evidence_txid, json.dumps([evidence_txid]), now, intent_id),
                )
                return
            if operation["status"] == "success":
                return
            conn.execute(
                "UPDATE operations SET status='failed',error_message=?,updated_at=? "
                "WHERE operation_id=? AND status IN ('queued','executing')",
                (_LAUNCH_UNKNOWN_ERROR, now, intent_id),
            )
            if submission["status"] in {"kept", "refunded", "refund_broadcast"}:
                return
            updates = {
                "status_detail": "Wallet launch outcome is unknown; manual reconciliation is required."
            }
            if kind == "refund":
                updates["refund_error"] = _LAUNCH_UNKNOWN_ERROR
            try:
                self.db.transition_in_connection(
                    conn,
                    submission_id,
                    "failure",
                    reason=f"{kind}_launch_outcome_unknown",
                    now=now,
                    updates=updates,
                )
            except StateConflict:
                pass

    def demo_send(self, public_id: str) -> dict[str, Any]:
        self._reject_snapshot_mutation()
        self._validate_public_id(public_id)
        row = self.db.get_submission(public_id)
        if row["status"] not in {"awaiting_bond", "mismatch", "failure"} or row["bond_txid"]:
            raise InvalidAction("this invoice cannot receive another demo bond")
        now = utc_now()
        if bool(row["demo"]):
            operation_id = mock_operation_id()
            txid = mock_txid()
            with self.db.transaction(immediate=True) as conn:
                current = self.db.get_submission_by_id(row["id"], conn)
                if current["status"] not in {"awaiting_bond", "mismatch", "failure"} or current["bond_txid"]:
                    raise InvalidAction("this invoice was already paid")
                conn.execute(
                    "INSERT INTO operations "
                    "(operation_id,submission_id,kind,wallet_role,status,txid,txids_json,broadcast,created_at,updated_at) "
                    "VALUES (?,?, 'bond_send','mock','success',?,?,1,?,?)",
                    (operation_id, row["id"], txid, json.dumps([txid]), now, now),
                )
                conn.execute(
                    "INSERT INTO wallet_outputs "
                    "(txid,pool,output_index,submission_id,to_address,value_zat,memo,is_change,mined_height,"
                    "confirmations,tx_status,match_result,mismatch_reason,first_seen_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,0,NULL,0,'waiting','exact',NULL,?,?)",
                    (txid, "orchard", 0, row["id"], row["invoice_address"], row["amount_zat"], row["memo"], now, now),
                )
                self.db.transition_in_connection(
                    conn,
                    row["id"],
                    "bond_pending",
                    reason="mock_bond_seen",
                    now=now,
                    metadata={"txid": txid},
                    updates={
                        "bond_txid": txid,
                        "bond_pool": "orchard",
                        "bond_output_index": 0,
                        "bond_confirmations": 0,
                        "bond_tx_status": "waiting",
                        "mismatch_reason": None,
                        "status_detail": "Mock payment seen; sync to simulate confirmation.",
                    },
                )
            row = self.db.get_submission(public_id)
            operation = {
                "operation_id": operation_id,
                "kind": "bond_send",
                "status": "success",
                "txid": txid,
                "broadcast": True,
                "error_message": None,
            }
            return {
                "mode": "mock",
                "mode_label": self.mode_label,
                "submission": self.serialize_submission(row, include_timeline=True),
                "operation": self._operation_public(operation, demo=True),
            }

        if not self.settings.live_sends_enabled:
            raise FeatureDisabled(
                "live participant sends are disabled; set HUSHBOARD_ENABLE_LIVE_SENDS=1 deliberately"
            )
        reserved, operation, launch_required = self._reserve_bond_launch(row["id"])
        if not launch_required:
            if self._is_launch_intent(operation["operation_id"]) and operation[
                "status"
            ] in {"queued", "executing"}:
                # The winning request may replace this provisional primary key with
                # Zallet's operation id at any moment. Do not hand a caller an id that
                # can disappear; a 409 tells it to retry the resource after the launch.
                raise InvalidAction("wallet launch is already in progress; retry shortly")
            return {
                "mode": "live",
                "mode_label": self.mode_label,
                "submission": self.serialize_submission(reserved, include_timeline=True),
                "operation": self._operation_public(operation, demo=False),
            }

        intent_id = operation["operation_id"]
        try:
            try:
                launched = self.wallet.send_bond(reserved["invoice_address"], reserved["memo"])
            except WalletError as exc:
                self._mark_launch_unknown(
                    intent_id=intent_id,
                    submission_id=reserved["id"],
                    kind="bond_send",
                )
                raise WalletUnavailable(_safe_error(exc)) from exc
            updated, operation = self._attach_wallet_operation(
                intent_id=intent_id,
                submission_id=reserved["id"],
                kind="bond_send",
                launched=launched,
            )
        finally:
            with self._launch_lock:
                self._launching_intents.discard(intent_id)
        return {
            "mode": "live",
            "mode_label": self.mode_label,
            "submission": self.serialize_submission(updated, include_timeline=True),
            "operation": self._operation_public(operation, demo=False),
        }

    def moderate(self, public_id: str, *, decision: str, note: str | None) -> dict[str, Any]:
        self._reject_snapshot_mutation()
        self._validate_public_id(public_id)
        row = self.db.get_submission(public_id)
        if decision not in {"refund", "keep"}:
            raise InputRejected("decision must be refund or keep")
        # Mock decisions and keep decisions remain single-shot. A live refund retry is
        # handled by its durable operation intent below and returns that same intent.
        if (decision == "keep" or bool(row["demo"])) and (
            row["status"] != "moderation" or row["moderation_decision"] is not None
        ):
            raise InvalidAction("only an undecided, confirmed submission can be moderated")
        now = utc_now()
        if decision == "keep":
            # Re-check under SQLite's write lock so two moderator clicks cannot race.
            with self.db.transaction(immediate=True) as conn:
                current = self.db.get_submission_by_id(row["id"], conn)
                if current["status"] != "moderation" or current["moderation_decision"] is not None:
                    raise InvalidAction("submission was already moderated")
                updated = self.db.transition_in_connection(
                    conn,
                    row["id"],
                    "kept",
                    reason="moderator_kept_bond",
                    now=now,
                    updates={
                        "moderation_decision": "keep",
                        "moderation_note": note,
                        "moderated_at": now,
                        "status_detail": "Moderator kept the bond.",
                    },
                )
            return {
                "mode": "mock" if bool(updated["demo"]) else "live",
                "submission": self.serialize_submission(updated, include_timeline=True),
                "operation": None,
            }
        if bool(row["demo"]):
            operation_id = mock_operation_id()
            txid = mock_txid()
            with self.db.transaction(immediate=True) as conn:
                current = self.db.get_submission_by_id(row["id"], conn)
                if current["status"] != "moderation" or current["moderation_decision"] is not None:
                    raise InvalidAction("submission was already moderated")
                conn.execute(
                    "INSERT INTO operations "
                    "(operation_id,submission_id,kind,wallet_role,status,txid,txids_json,broadcast,created_at,updated_at) "
                    "VALUES (?,?, 'refund','mock','success',?,?,1,?,?)",
                    (operation_id, row["id"], txid, json.dumps([txid]), now, now),
                )
                updated = self.db.transition_in_connection(
                    conn,
                    row["id"],
                    "refund_broadcast",
                    reason="mock_refund_broadcast",
                    now=now,
                    metadata={"txid": txid},
                    updates={
                        "moderation_decision": "refund",
                        "moderation_note": note,
                        "moderated_at": now,
                        "refund_operation_id": operation_id,
                        "refund_txid": txid,
                        "refund_confirmations": 0,
                        "refund_tx_status": "waiting",
                        "refund_error": None,
                        "status_detail": "Mock refund broadcast; sync to simulate confirmation.",
                    },
                )
            operation = {
                "operation_id": operation_id,
                "kind": "refund",
                "status": "success",
                "txid": txid,
                "broadcast": True,
                "error_message": None,
            }
            return {
                "mode": "mock",
                "submission": self.serialize_submission(updated, include_timeline=True),
                "operation": self._operation_public(operation, demo=True),
            }

        if not self.settings.live_sends_enabled:
            raise FeatureDisabled(
                "live refunds are disabled; set HUSHBOARD_ENABLE_LIVE_SENDS=1 deliberately"
            )
        # The moderator decision and a unique local operation intent commit together.
        # `refund_broadcast` remains evidence-bearing and is entered only after Zallet
        # reports success with a concrete txid and does not report broadcast=false.
        reserved, operation, launch_required = self._reserve_refund_launch(
            row["id"], note=note
        )
        if not launch_required:
            if self._is_launch_intent(operation["operation_id"]) and operation[
                "status"
            ] in {"queued", "executing"}:
                raise InvalidAction("wallet launch is already in progress; retry shortly")
            return {
                "mode": "live",
                "submission": self.serialize_submission(reserved, include_timeline=True),
                "operation": self._operation_public(operation, demo=False),
            }

        intent_id = operation["operation_id"]
        try:
            try:
                launched = self.wallet.send_refund(
                    reserved["refund_address"], reserved["memo"]
                )
            except WalletError as exc:
                self._mark_launch_unknown(
                    intent_id=intent_id,
                    submission_id=reserved["id"],
                    kind="refund",
                )
                raise WalletUnavailable(_safe_error(exc)) from exc
            updated, operation = self._attach_wallet_operation(
                intent_id=intent_id,
                submission_id=reserved["id"],
                kind="refund",
                launched=launched,
            )
        finally:
            with self._launch_lock:
                self._launching_intents.discard(intent_id)
        return {
            "mode": "live",
            "submission": self.serialize_submission(updated, include_timeline=True),
            "operation": self._operation_public(operation, demo=False),
        }

    def sync(self) -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            raise InvalidAction("a wallet sync is already running")
        try:
            counters: dict[str, Any] = {
                "ok": True,
                "mode": self.resolve_mode(),
                "scanned_transactions": 0,
                "scanned_outputs": 0,
                "matched": 0,
                "mismatched": 0,
                "operations_updated": 0,
                "submissions_updated": 0,
                "warnings": [],
            }
            if self.offline_snapshot:
                counters["read_only"] = True
                counters["warnings"].append("immutable offline replay; sync skipped")
                counters["synced_at"] = self.db.get_meta("last_sync_at")
                self.watcher_error = None
                return counters
            # Mock rows remain deterministic even if an operator later changes the
            # deployment to live mode; they never touch a wallet.
            self._sync_mock(counters)
            if self.resolve_mode() == "live":
                self._poll_operations(counters)
                self._scan_operator_transactions(counters)
                self._reconcile_refunds(counters)
                # Give normal operation polling and on-chain receipt matching the first
                # chance to supply evidence before classifying an orphaned local intent.
                self._reconcile_launch_intents(counters)
            now = utc_now()
            self.db.set_meta("last_sync_at", now, now=now)
            counters["synced_at"] = now
            self.watcher_error = None
            return counters
        except WalletError as exc:
            self.watcher_error = _safe_error(exc)
            raise WalletUnavailable(self.watcher_error) from exc
        finally:
            self._sync_lock.release()

    def _sync_mock(self, counters: dict[str, Any]) -> None:
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            pending = conn.execute(
                "SELECT * FROM submissions WHERE demo=1 AND status='bond_pending' AND bond_txid IS NOT NULL"
            ).fetchall()
            for row in pending:
                conn.execute(
                    "UPDATE wallet_outputs SET confirmations=?,mined_height=1,tx_status='mined',last_seen_at=? "
                    "WHERE submission_id=? AND txid=? AND pool=? AND output_index=?",
                    (
                        self.settings.minimum_confirmations, now, row["id"], row["bond_txid"],
                        row["bond_pool"], row["bond_output_index"],
                    ),
                )
                self.db.transition_in_connection(
                    conn,
                    row["id"],
                    "moderation",
                    reason="mock_bond_confirmed",
                    now=now,
                    updates={
                        "bond_confirmations": self.settings.minimum_confirmations,
                        "bond_mined_height": 1,
                        "bond_tx_status": "mined",
                        "status_detail": "Mock bond confirmed; awaiting centralized moderation.",
                    },
                )
                counters["matched"] += 1
                counters["submissions_updated"] += 1
            refunds = conn.execute(
                "SELECT * FROM submissions WHERE demo=1 AND status='refund_broadcast' AND refund_txid IS NOT NULL"
            ).fetchall()
            for row in refunds:
                self.db.transition_in_connection(
                    conn,
                    row["id"],
                    "refunded",
                    reason="mock_refund_confirmed",
                    now=now,
                    updates={
                        "refund_confirmations": self.settings.minimum_confirmations,
                        "refund_tx_status": "mined",
                        "status_detail": "Mock refund confirmed.",
                    },
                )
                counters["submissions_updated"] += 1

    def _reconcile_launch_intents(self, counters: dict[str, Any]) -> None:
        """Fail closed after an orphaned launch, but let later evidence repair it."""
        with self._launch_lock:
            locally_launching = set(self._launching_intents)
        grace_seconds = max(30.0, self.settings.rpc_timeout + 5.0)
        now_dt = datetime.now(UTC)
        # Failed provisional ids remain deliberately non-retryable, but they must stay
        # eligible for repair if a later wallet scan proves that the timed-out launch
        # actually reached the chain.
        with self.db.connection() as conn:
            intents = conn.execute(
                "SELECT * FROM operations WHERE operation_id GLOB 'intent-*' "
                "AND status IN ('queued','executing','failed') ORDER BY created_at"
            ).fetchall()
        for op in intents:
            if op["operation_id"] in locally_launching:
                continue
            try:
                created = datetime.fromisoformat(str(op["created_at"]))
                stale = (now_dt - created).total_seconds() >= grace_seconds
            except (TypeError, ValueError):
                stale = True

            now = utc_now()
            with self.db.transaction(immediate=True) as conn:
                current_op = conn.execute(
                    "SELECT * FROM operations WHERE operation_id=?",
                    (op["operation_id"],),
                ).fetchone()
                if current_op is None or current_op["status"] not in {
                    "queued",
                    "executing",
                    "failed",
                }:
                    continue
                submission = self.db.get_submission_by_id(current_op["submission_id"], conn)
                evidence_txid = self._mined_evidence_txid(
                    submission, current_op["kind"]
                )
                if evidence_txid:
                    if self._operation_accepts_late_evidence(current_op):
                        conn.execute(
                            "UPDATE operations SET status='success',txid=?,txids_json=?,"
                            "broadcast=1,error_code=NULL,error_message=NULL,missing_count=0,"
                            "updated_at=? WHERE operation_id=?",
                            (
                                evidence_txid,
                                json.dumps([evidence_txid]),
                                now,
                                current_op["operation_id"],
                            ),
                        )
                        counters["operations_updated"] += 1
                    continue
                if current_op["status"] == "failed":
                    # This is the durable fail-closed state: never manufacture a new
                    # wallet call merely because reconciliation still lacks evidence.
                    continue
                if not stale:
                    counters["warnings"].append(
                        "wallet launch reservation is awaiting reconciliation"
                    )
                    continue

                conn.execute(
                    "UPDATE operations SET status='failed',error_message=?,updated_at=? "
                    "WHERE operation_id=?",
                    (_LAUNCH_UNKNOWN_ERROR, now, current_op["operation_id"]),
                )
                counters["operations_updated"] += 1
                if submission["status"] in {"kept", "refunded", "refund_broadcast"}:
                    continue
                updates = {
                    "status_detail": (
                        "Wallet launch outcome is unknown; manual reconciliation is required."
                    )
                }
                if current_op["kind"] == "refund":
                    updates["refund_error"] = _LAUNCH_UNKNOWN_ERROR
                try:
                    self.db.transition_in_connection(
                        conn,
                        submission["id"],
                        "failure",
                        reason=f"{current_op['kind']}_launch_intent_orphaned",
                        now=now,
                        updates=updates,
                    )
                    counters["submissions_updated"] += 1
                except StateConflict:
                    pass

    def _poll_operations(self, counters: dict[str, Any]) -> None:
        active = [
            op
            for op in self.db.active_operations()
            if op["wallet_role"] != "mock"
            and not self._is_launch_intent(op["operation_id"])
        ]
        for role in ("operator", "participant"):
            role_ops = [op for op in active if op["wallet_role"] == role]
            if not role_ops:
                continue
            statuses = self.wallet.operation_statuses(role, [op["operation_id"] for op in role_ops])
            by_id = {
                item.get("id", item.get("operationid")): item
                for item in statuses
                if isinstance(item.get("id", item.get("operationid")), str)
            }
            for op in role_ops:
                item = by_id.get(op["operation_id"])
                if item is None:
                    self._mark_operation_missing(op, counters)
                else:
                    self._apply_operation_status(op, item, counters)

    def _mark_operation_missing(self, op: sqlite3.Row, counters: dict[str, Any]) -> None:
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            current = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?", (op["operation_id"],)
            ).fetchone()
            if current is None or current["status"] not in {"queued", "executing"}:
                return
            missing = current["missing_count"] + 1
            if missing < 3:
                conn.execute(
                    "UPDATE operations SET missing_count=?,updated_at=? WHERE operation_id=?",
                    (missing, now, op["operation_id"]),
                )
                return
            error = _OPERATION_MISSING_ERROR
            conn.execute(
                "UPDATE operations SET status='failed',missing_count=?,error_message=?,updated_at=? "
                "WHERE operation_id=?",
                (missing, error, now, op["operation_id"]),
            )
            submission = self.db.get_submission_by_id(op["submission_id"], conn)
            if submission["status"] not in {"kept", "refunded"}:
                try:
                    self.db.transition_in_connection(
                        conn,
                        submission["id"],
                        "failure",
                        reason="wallet_operation_lost",
                        now=now,
                        updates={
                            "refund_error": error if op["kind"] == "refund" else submission["refund_error"],
                            "status_detail": "Wallet operation needs manual reconciliation.",
                        },
                    )
                    counters["submissions_updated"] += 1
                except StateConflict:
                    pass
            counters["operations_updated"] += 1

    def _apply_operation_status(
        self, op: sqlite3.Row, item: dict[str, Any], counters: dict[str, Any]
    ) -> None:
        raw_status = str(item.get("status", "")).lower()
        status = {
            "ready": "queued",
            "queued": "queued",
            "executing": "executing",
            "success": "success",
            "failed": "failed",
            "cancelled": "cancelled",
            "canceled": "cancelled",
        }.get(raw_status)
        if status is None:
            counters["warnings"].append(f"unknown operation status for {op['operation_id']}")
            return
        result = item.get("result")
        txid, txids, broadcast = extract_operation_result(result)
        error_obj = item.get("error")
        error_code = error_obj.get("code") if isinstance(error_obj, dict) and isinstance(error_obj.get("code"), int) else None
        error_message = None
        if isinstance(error_obj, dict):
            # Operation status is an untrusted wallet response and can echo recipient UAs.
            # Persist only the numeric code, never its raw message/body.
            error_message = (
                f"wallet operation failed (code {error_code})"
                if error_code is not None
                else "wallet operation failed"
            )
        if status == "success" and broadcast is False:
            status = "failed"
            error_message = "Zallet built the transaction but external.broadcast was false"
        if status == "success" and not txid:
            status = "failed"
            error_message = "successful operation did not expose a single transaction id"
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            current = conn.execute(
                "SELECT * FROM operations WHERE operation_id=?", (op["operation_id"],)
            ).fetchone()
            if current is None or current["status"] not in {"queued", "executing"}:
                return
            conn.execute(
                "UPDATE operations SET status=?,txid=?,txids_json=?,broadcast=?,error_code=?,"
                "error_message=?,missing_count=0,updated_at=? WHERE operation_id=?",
                (
                    status,
                    txid,
                    json.dumps(txids) if txids else None,
                    None if broadcast is None else int(broadcast),
                    error_code,
                    error_message,
                    now,
                    op["operation_id"],
                ),
            )
            submission = self.db.get_submission_by_id(op["submission_id"], conn)
            if status in {"failed", "cancelled"} and submission["status"] not in {"kept", "refunded"}:
                target_error = error_message or f"wallet operation {status}"
                try:
                    self.db.transition_in_connection(
                        conn,
                        submission["id"],
                        "failure",
                        reason=f"{op['kind']}_operation_{status}",
                        now=now,
                        updates={
                            "refund_error": target_error if op["kind"] == "refund" else submission["refund_error"],
                            "status_detail": "Wallet operation failed; no automatic retry was attempted.",
                        },
                    )
                    counters["submissions_updated"] += 1
                except StateConflict:
                    pass
            elif status == "success" and op["kind"] == "refund":
                if (
                    submission["status"] == "moderation"
                    and submission["moderation_decision"] == "refund"
                    and txid is not None
                    and broadcast is not False
                ):
                    self.db.transition_in_connection(
                        conn,
                        submission["id"],
                        "refund_broadcast",
                        reason="refund_operation_broadcast",
                        now=now,
                        metadata={"txid": txid},
                        updates={
                            "refund_txid": txid,
                            "refund_tx_status": "waiting",
                            "refund_error": None,
                            "status_detail": "Refund broadcast; awaiting confirmation.",
                        },
                    )
                    counters["submissions_updated"] += 1
            counters["operations_updated"] += 1

    @staticmethod
    def _memo_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        if value.startswith("HB1:"):
            return value.rstrip("\x00")
        if len(value) % 2 == 0 and value:
            try:
                decoded = bytes.fromhex(value).rstrip(b"\x00").decode("utf-8")
            except (ValueError, UnicodeError):
                return value
            return decoded
        return value

    def _transaction_state(self, txid: str, tx: dict[str, Any]) -> tuple[str, int]:
        try:
            detail = self.wallet.view_transaction("operator", txid)
            status = str(detail.get("status", "waiting")).lower()
            confirmations = detail.get("confirmations", 0)
            if not isinstance(confirmations, int) or isinstance(confirmations, bool):
                confirmations = 0
            return status, max(0, confirmations)
        except WalletError:
            # z_listtransactions already proves wallet relevance. Older beta builds may
            # briefly fail z_viewtransaction while indexing, so retain a conservative
            # mined/not-mined view instead of inventing confirmations.
            return (
                ("mined", 0)
                if self._is_positive_int(tx.get("mined_height"))
                else ("waiting", 0)
            )

    def _scan_operator_transactions(self, counters: dict[str, Any]) -> None:
        transactions = self.wallet.list_operator_transactions()
        counters["scanned_transactions"] += len(transactions)
        with self.db.connection() as conn:
            invoice_rows = conn.execute("SELECT * FROM submissions WHERE demo=0").fetchall()
            refund_rows = conn.execute(
                "SELECT DISTINCT s.* FROM submissions AS s "
                "JOIN operations AS o ON o.submission_id=s.id AND o.kind='refund' "
                "WHERE s.demo=0 AND s.moderation_decision='refund' "
                "AND o.status IN ('queued','executing','failed')"
            ).fetchall()
        by_address = {row["invoice_address"]: row for row in invoice_rows}
        refunds_by_address: dict[str, list[sqlite3.Row]] = {}
        for row in refund_rows:
            refunds_by_address.setdefault(row["refund_address"], []).append(row)
        tx_states: dict[str, tuple[str, int]] = {}
        for tx in transactions:
            txid = tx.get("txid")
            if not isinstance(txid, str) or not _TXID_RE.fullmatch(txid.lower()):
                counters["warnings"].append("wallet returned a transaction with an invalid txid")
                continue
            txid = txid.lower()
            outputs = tx.get("outputs")
            if not isinstance(outputs, list):
                continue
            state = tx_states.setdefault(txid, self._transaction_state(txid, tx))
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                counters["scanned_outputs"] += 1
                address = output.get("to_address")
                submission = by_address.get(address)
                if submission is not None:
                    self._ingest_invoice_output(submission, txid, tx, output, state, counters)
                for refund in refunds_by_address.get(address, ()):
                    self._ingest_refund_launch_evidence(
                        refund, txid, tx, output, state, counters
                    )

    def _ingest_refund_launch_evidence(
        self,
        submission: sqlite3.Row,
        txid: str,
        tx: dict[str, Any],
        output: dict[str, Any],
        tx_state: tuple[str, int],
        counters: dict[str, Any],
    ) -> None:
        """Repair an ambiguous refund only from an exact mined outgoing note."""
        status, confirmations = tx_state
        pool = str(output.get("pool", "unknown")).lower()
        output_index = output.get("output_index")
        value = output.get("value")
        memo = self._memo_text(output.get("memo"))
        account_uuid = self._canonical_account_uuid(tx.get("account_uuid"))
        if (
            status != "mined"
            or not self._is_positive_int(tx.get("mined_height"))
            or account_uuid is None
            or not self._is_positive_int(tx.get("sent_note_count"))
            or tx.get("expired_unmined") is not False
            or pool not in {"orchard", "ironwood"}
            or not isinstance(output_index, int)
            or isinstance(output_index, bool)
            or output_index < 0
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value != submission["amount_zat"]
            or memo != submission["memo"]
            or output.get("to_address") != submission["refund_address"]
            or output.get("from_account") != account_uuid
            or "to_account" not in output
            or output["to_account"] is not None
            or output.get("is_change") is not False
        ):
            return

        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            current = self.db.get_submission_by_id(submission["id"], conn)
            if (
                current["moderation_decision"] != "refund"
                or current["refund_operation_id"] is None
                or txid == current["bond_txid"]
                or current["refund_txid"] not in {None, txid}
                or current["status"] not in {
                    "moderation",
                    "failure",
                    "refund_broadcast",
                    "refunded",
                }
            ):
                return
            operation = conn.execute(
                "SELECT * FROM operations WHERE operation_id=? AND kind='refund'",
                (current["refund_operation_id"],),
            ).fetchone()
            if operation is None or operation["txid"] not in {None, txid}:
                return
            if not self._operation_accepts_late_evidence(operation):
                return
            conn.execute(
                "UPDATE operations SET status='success',txid=?,txids_json=?,broadcast=1,"
                "error_code=NULL,error_message=NULL,missing_count=0,updated_at=? "
                "WHERE operation_id=?",
                (txid, json.dumps([txid]), now, operation["operation_id"]),
            )
            counters["operations_updated"] += 1
            if current["status"] == "refunded":
                return
            self.db.transition_in_connection(
                conn,
                current["id"],
                "refund_broadcast",
                reason="refund_launch_reconciled_from_mined_output",
                now=now,
                metadata={"txid": txid, "pool": pool, "output_index": output_index},
                updates={
                    "refund_txid": txid,
                    "refund_confirmations": confirmations,
                    "refund_tx_status": status,
                    "refund_error": None,
                    "status_detail": "Mined refund evidence reconciled after an ambiguous wallet launch.",
                },
            )
            counters["submissions_updated"] += 1

    def _ingest_invoice_output(
        self,
        submission: sqlite3.Row,
        txid: str,
        tx: dict[str, Any],
        output: dict[str, Any],
        tx_state: tuple[str, int],
        counters: dict[str, Any],
    ) -> None:
        pool = str(output.get("pool", "unknown")).lower()
        output_index = output.get("output_index")
        value = output.get("value")
        if not isinstance(output_index, int) or isinstance(output_index, bool) or output_index < 0:
            counters["warnings"].append(f"invalid output index in {txid}")
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            counters["warnings"].append(f"invalid output value in {txid}")
            return
        memo = self._memo_text(output.get("memo"))
        change_flag = output.get("is_change")
        is_change = change_flag is True
        status, confirmations = tx_state
        mined_height = (
            tx.get("mined_height")
            if self._is_positive_int(tx.get("mined_height"))
            else None
        )
        account_uuid = self._canonical_account_uuid(tx.get("account_uuid"))
        reasons: list[str] = []
        # Orchard receiver funds move into the Ironwood pool after NU6.3. Both are the
        # same Orchard-family receiver, never a request for an "ironwood" UA receiver.
        if pool not in {"orchard", "ironwood"}:
            reasons.append(f"unexpected_pool:{pool}")
        if output.get("to_address") != submission["invoice_address"]:
            reasons.append("wrong_receiver")
        if value != submission["amount_zat"]:
            reasons.append(f"wrong_amount:{value}")
        if memo != submission["memo"]:
            reasons.append("memo_missing" if memo is None else "memo_mismatch")
        if account_uuid is None:
            reasons.append("invalid_account_uuid")
        if not self._is_positive_int(tx.get("received_note_count")):
            reasons.append("not_received_by_account")
        if tx.get("expired_unmined") is not False:
            reasons.append("expired_or_unknown_transaction")
        if "from_account" not in output or output["from_account"] is not None:
            reasons.append("not_incoming")
        if account_uuid is None or output.get("to_account") != account_uuid:
            reasons.append("wrong_destination_account")
        if change_flag is not False:
            reasons.append("change_output" if is_change else "invalid_change_flag")
        exact = not reasons
        now = utc_now()
        with self.db.transaction(immediate=True) as conn:
            current_output = conn.execute(
                "SELECT * FROM wallet_outputs WHERE txid=? AND pool=? AND output_index=?",
                (txid, pool, output_index),
            ).fetchone()
            initial_result = "exact" if exact else "mismatch"
            if current_output is None:
                conn.execute(
                    "INSERT INTO wallet_outputs "
                    "(txid,pool,output_index,submission_id,to_address,value_zat,memo,is_change,mined_height,"
                    "confirmations,tx_status,match_result,mismatch_reason,first_seen_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        txid, pool, output_index, submission["id"], output.get("to_address"), value,
                        memo, int(is_change), mined_height, confirmations, status,
                        initial_result, ";".join(reasons) or None, now, now,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE wallet_outputs SET mined_height=?,confirmations=?,tx_status=?,last_seen_at=? "
                    "WHERE id=?",
                    (mined_height, confirmations, status, now, current_output["id"]),
                )
            current = self.db.get_submission_by_id(submission["id"], conn)
            bound_same = (
                current["bond_txid"] == txid
                and current["bond_pool"] == pool
                and current["bond_output_index"] == output_index
            )
            if exact and current["bond_txid"] is None and current["status"] not in {"kept", "refunded", "refund_broadcast"}:
                if current["status"] not in {"awaiting_bond", "mismatch", "failure", "bond_pending"}:
                    conn.execute(
                        "UPDATE wallet_outputs SET match_result='duplicate_exact' WHERE txid=? AND pool=? AND output_index=?",
                        (txid, pool, output_index),
                    )
                    return
                if current["status"] != "bond_pending":
                    current = self.db.transition_in_connection(
                        conn,
                        current["id"],
                        "bond_pending",
                        reason="exact_bond_seen",
                        now=now,
                        metadata={"txid": txid, "pool": pool, "output_index": output_index},
                        updates={
                            "bond_txid": txid,
                            "bond_pool": pool,
                            "bond_output_index": output_index,
                            "bond_confirmations": confirmations,
                            "bond_mined_height": mined_height,
                            "bond_tx_status": status,
                            "mismatch_reason": None,
                            "status_detail": "Exact bond seen; awaiting confirmations.",
                        },
                    )
                    counters["submissions_updated"] += 1
                bound_same = True
            elif exact and current["bond_txid"] is not None and not bound_same:
                conn.execute(
                    "UPDATE wallet_outputs SET match_result='duplicate_exact' WHERE txid=? AND pool=? AND output_index=?",
                    (txid, pool, output_index),
                )
                return

            if exact and bound_same:
                current = self.db.get_submission_by_id(submission["id"], conn)
                if status == "mined" and mined_height is not None:
                    self._repair_operation_from_chain_evidence(
                        conn,
                        submission_id=current["id"],
                        kind="bond_send",
                        txid=txid,
                        counters=counters,
                    )
                if status in {"expired", "cancelled"}:
                    if current["status"] in {"bond_pending", "moderation"}:
                        self.db.transition_in_connection(
                            conn,
                            current["id"],
                            "failure",
                            reason="bond_transaction_expired",
                            now=now,
                            updates={
                                "bond_confirmations": 0,
                                "bond_tx_status": status,
                                "status_detail": "Bond transaction expired or was invalidated.",
                            },
                        )
                        counters["submissions_updated"] += 1
                elif (
                    status == "mined"
                    and mined_height is not None
                    and confirmations >= self.settings.minimum_confirmations
                ):
                    if current["status"] == "bond_pending":
                        self.db.transition_in_connection(
                            conn,
                            current["id"],
                            "moderation",
                            reason="bond_confirmed",
                            now=now,
                            metadata={"txid": txid, "confirmations": confirmations},
                            updates={
                                "bond_confirmations": confirmations,
                                "bond_mined_height": mined_height,
                                "bond_tx_status": status,
                                "status_detail": "Bond confirmed; awaiting centralized moderation.",
                            },
                        )
                        counters["submissions_updated"] += 1
                    elif current["status"] == "moderation":
                        self.db.transition_in_connection(
                            conn,
                            current["id"],
                            "moderation",
                            reason="bond_confirmation_refresh",
                            now=now,
                            updates={
                                "bond_confirmations": confirmations,
                                "bond_mined_height": mined_height,
                                "bond_tx_status": status,
                            },
                        )
                else:
                    if current["status"] == "moderation" and current["moderation_decision"] is None:
                        self.db.transition_in_connection(
                            conn,
                            current["id"],
                            "bond_pending",
                            reason="bond_reorg_or_confirmation_drop",
                            now=now,
                            updates={
                                "bond_confirmations": confirmations,
                                "bond_tx_status": status,
                                "status_detail": "Bond confirmation dropped; waiting again.",
                            },
                        )
                        counters["submissions_updated"] += 1
                    elif current["status"] == "bond_pending":
                        self.db.transition_in_connection(
                            conn,
                            current["id"],
                            "bond_pending",
                            reason="bond_confirmation_refresh",
                            now=now,
                            updates={"bond_confirmations": confirmations, "bond_tx_status": status},
                        )
                counters["matched"] += 1
            elif not exact:
                reason = ";".join(reasons)
                if current["status"] in {"awaiting_bond", "mismatch"} and not current["bond_txid"]:
                    self.db.transition_in_connection(
                        conn,
                        current["id"],
                        "mismatch",
                        reason="invoice_output_mismatch",
                        now=now,
                        metadata={"txid": txid, "pool": pool, "output_index": output_index},
                        updates={
                            "mismatch_reason": reason,
                            "status_detail": "A payment arrived, but amount or memo did not match the invoice.",
                        },
                    )
                    counters["submissions_updated"] += 1
                counters["mismatched"] += 1

    def _reconcile_refunds(self, counters: dict[str, Any]) -> None:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM submissions WHERE demo=0 AND status='refund_broadcast' AND refund_txid IS NOT NULL"
            ).fetchall()
        for row in rows:
            try:
                detail = self.wallet.view_transaction("operator", row["refund_txid"])
            except WalletError as exc:
                counters["warnings"].append(f"refund {row['public_id']}: {_safe_error(exc)}")
                continue
            status = str(detail.get("status", "waiting")).lower()
            confirmations = detail.get("confirmations", 0)
            if not isinstance(confirmations, int) or isinstance(confirmations, bool):
                confirmations = 0
            confirmations = max(0, confirmations)
            now = utc_now()
            if status == "mined" and confirmations >= self.settings.minimum_confirmations:
                self.db.transition(
                    row["public_id"],
                    "refunded",
                    reason="refund_confirmed",
                    now=now,
                    metadata={"txid": row["refund_txid"], "confirmations": confirmations},
                    updates={
                        "refund_confirmations": confirmations,
                        "refund_tx_status": status,
                        "status_detail": "Refund confirmed.",
                    },
                )
                counters["submissions_updated"] += 1
            elif status in {"expired", "cancelled"}:
                self.db.transition(
                    row["public_id"],
                    "failure",
                    reason="refund_transaction_expired",
                    now=now,
                    updates={
                        "refund_confirmations": 0,
                        "refund_tx_status": status,
                        "refund_error": "refund transaction expired before confirmation",
                        "status_detail": "Refund transaction needs manual reconciliation.",
                    },
                )
                counters["submissions_updated"] += 1
            else:
                self.db.transition(
                    row["public_id"],
                    "refund_broadcast",
                    reason="refund_confirmation_refresh",
                    now=now,
                    updates={
                        "refund_confirmations": confirmations,
                        "refund_tx_status": status,
                    },
                )

    def reset(self) -> dict[str, Any]:
        if self.resolve_mode() != "mock":
            raise FeatureDisabled("database reset is available only in offline demo mode")
        self._reject_snapshot_mutation()
        deleted = self.db.reset()
        return {"ok": True, "mode": "mock", "deleted": deleted, "seeded": False}

    def seed(self, *, reset: bool, count: int) -> dict[str, Any]:
        if self.resolve_mode() != "mock":
            raise FeatureDisabled("seed data is available only in offline demo mode")
        self._reject_snapshot_mutation()
        if reset:
            self.db.reset()
        created_ids: list[str] = []
        for index in range(count):
            refund = mock_unified_address()
            created = self.create_submission(
                title=(
                    "Search result labels are unclear"
                    if index % 3 == 0
                    else "Please improve keyboard navigation"
                    if index % 3 == 1
                    else "Weekly digest would be useful"
                ),
                body=(
                    "A concise, accountless demo submission used to show the bond and moderation flow."
                    f" Sample item {index + 1}."
                ),
                refund_address=refund,
            )
            public_id = created["id"]
            created_ids.append(public_id)
            state = index % 8
            if state == 0:
                continue
            if state == 6:
                self._seed_mismatch(public_id)
                continue
            if state == 7:
                self.db.transition(
                    public_id,
                    "failure",
                    reason="seeded_demo_failure",
                    now=utc_now(),
                    updates={"status_detail": "Demo-only failure for the recovery UI."},
                )
                continue
            self.demo_send(public_id)
            if state == 1:
                continue
            self.sync()
            if state == 2:
                continue
            if state == 3:
                self.moderate(public_id, decision="keep", note="Useful feedback; bond retained by policy.")
            else:
                self.moderate(public_id, decision="refund", note="Constructive feedback; refund approved.")
                if state == 5:
                    self.sync()
        return {
            "ok": True,
            "mode": "mock",
            "created": [self.get_submission(public_id) for public_id in created_ids],
            "count": len(created_ids),
        }

    def _seed_mismatch(self, public_id: str) -> None:
        row = self.db.get_submission(public_id)
        now = utc_now()
        txid = mock_txid()
        with self.db.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO wallet_outputs "
                "(txid,pool,output_index,submission_id,to_address,value_zat,memo,is_change,mined_height,"
                "confirmations,tx_status,match_result,mismatch_reason,first_seen_at,last_seen_at) "
                "VALUES (?,?,?,?,?,?,?,0,1,1,'mined','mismatch','wrong_amount',?,?)",
                (
                    txid, "orchard", 0, row["id"], row["invoice_address"],
                    row["amount_zat"] - 1, row["memo"], now, now,
                ),
            )
            self.db.transition_in_connection(
                conn,
                row["id"],
                "mismatch",
                reason="seeded_payment_mismatch",
                now=now,
                metadata={"txid": txid},
                updates={
                    "mismatch_reason": "wrong_amount:999999",
                    "status_detail": "Demo payment was one zat short.",
                },
            )
