"""SQLite persistence and the HushBoard state-machine guardrails."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STATUSES = (
    "awaiting_bond",
    "bond_pending",
    "moderation",
    "refund_broadcast",
    "refunded",
    "kept",
    "mismatch",
    "failure",
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "awaiting_bond": frozenset({"bond_pending", "mismatch", "failure"}),
    "bond_pending": frozenset({"moderation", "mismatch", "failure", "awaiting_bond"}),
    "mismatch": frozenset({"awaiting_bond", "bond_pending", "moderation", "failure"}),
    "moderation": frozenset({"bond_pending", "refund_broadcast", "kept", "failure"}),
    "refund_broadcast": frozenset({"refunded", "failure"}),
    # A failure is explicitly recoverable, but only through a deliberate sync or retry.
    "failure": frozenset({"awaiting_bond", "bond_pending", "moderation", "refund_broadcast"}),
    "refunded": frozenset(),
    "kept": frozenset(),
}


class DatabaseError(RuntimeError):
    pass


class NotFound(DatabaseError):
    pass


class StateConflict(DatabaseError):
    def __init__(self, current: str, requested: str, message: str | None = None):
        self.current = current
        self.requested = requested
        super().__init__(message or f"cannot transition {current} to {requested}")


SCHEMA = r"""
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE CHECK(length(public_id) = 12),
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 4000),
    refund_address TEXT NOT NULL CHECK(length(refund_address) BETWEEN 20 AND 512),
    refund_address_hint TEXT NOT NULL,
    invoice_address TEXT NOT NULL UNIQUE,
    invoice_diversifier_index TEXT,
    zip321_uri TEXT NOT NULL,
    memo TEXT NOT NULL UNIQUE CHECK(length(memo) = 16),
    amount_zat INTEGER NOT NULL CHECK(amount_zat = 1000000),
    status TEXT NOT NULL CHECK(status IN (
        'awaiting_bond','bond_pending','moderation','refund_broadcast',
        'refunded','kept','mismatch','failure'
    )),
    status_detail TEXT,
    bond_txid TEXT,
    bond_pool TEXT,
    bond_output_index INTEGER,
    bond_confirmations INTEGER NOT NULL DEFAULT 0 CHECK(bond_confirmations >= 0),
    bond_mined_height INTEGER,
    bond_tx_status TEXT,
    mismatch_reason TEXT,
    refund_operation_id TEXT UNIQUE,
    refund_txid TEXT,
    refund_confirmations INTEGER NOT NULL DEFAULT 0 CHECK(refund_confirmations >= 0),
    refund_tx_status TEXT,
    refund_error TEXT,
    moderation_decision TEXT CHECK(moderation_decision IN ('refund','keep') OR moderation_decision IS NULL),
    moderation_note TEXT,
    moderated_at TEXT,
    demo INTEGER NOT NULL DEFAULT 0 CHECK(demo IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS submissions_status_created_idx
    ON submissions(status, created_at DESC);
CREATE INDEX IF NOT EXISTS submissions_created_idx
    ON submissions(created_at DESC);
CREATE INDEX IF NOT EXISTS submissions_bond_txid_idx
    ON submissions(bond_txid) WHERE bond_txid IS NOT NULL;
CREATE INDEX IF NOT EXISTS submissions_refund_txid_idx
    ON submissions(refund_txid) WHERE refund_txid IS NOT NULL;

CREATE TABLE IF NOT EXISTS wallet_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txid TEXT NOT NULL,
    pool TEXT NOT NULL,
    output_index INTEGER NOT NULL CHECK(output_index >= 0),
    submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
    to_address TEXT,
    value_zat INTEGER NOT NULL CHECK(value_zat >= 0),
    memo TEXT,
    is_change INTEGER NOT NULL DEFAULT 0 CHECK(is_change IN (0,1)),
    mined_height INTEGER,
    confirmations INTEGER NOT NULL DEFAULT 0 CHECK(confirmations >= 0),
    tx_status TEXT,
    match_result TEXT NOT NULL CHECK(match_result IN ('exact','mismatch','duplicate_exact','ignored')),
    mismatch_reason TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(txid, pool, output_index)
);
CREATE INDEX IF NOT EXISTS wallet_outputs_submission_idx
    ON wallet_outputs(submission_id, first_seen_at);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    request_key TEXT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('bond_send','refund')),
    wallet_role TEXT NOT NULL CHECK(wallet_role IN ('operator','participant','mock')),
    status TEXT NOT NULL CHECK(status IN ('queued','executing','success','failed','cancelled')),
    txid TEXT,
    txids_json TEXT,
    broadcast INTEGER CHECK(broadcast IN (0,1) OR broadcast IS NULL),
    error_code INTEGER,
    error_message TEXT,
    missing_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS operations_active_idx
    ON operations(status, wallet_role);
CREATE INDEX IF NOT EXISTS operations_submission_idx
    ON operations(submission_id, created_at);

CREATE TABLE IF NOT EXISTS state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS state_events_submission_idx
    ON state_events(submission_id, id);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _secure_permissions(self) -> None:
        # The DB contains plaintext feedback and full refund UAs. It must not inherit a
        # permissive 0644 umask, including SQLite's WAL/shared-memory sidecars.
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                candidate.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        self._secure_permissions()
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Pre-create with a private mode before SQLite opens it. chmod also repairs a DB
        # produced by an older HushBoard build under a permissive process umask.
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        conn = self._connect()
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > 2:
                raise DatabaseError(
                    f"database schema version {version} is newer than supported version 2"
                )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            if version < 2:
                conn.executescript(SCHEMA)

            # v2 adds a stable idempotency key without rebuilding the operations table.
            # The write lock makes concurrent v1 processes serialize their column check
            # and lets the loser validate the v2 schema written by the winner.
            conn.execute("BEGIN IMMEDIATE")
            try:
                version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                if version > 2:
                    raise DatabaseError(
                        f"database schema version {version} is newer than supported version 2"
                    )
                operation_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(operations)").fetchall()
                }
                if version < 2:
                    if "request_key" not in operation_columns:
                        conn.execute("ALTER TABLE operations ADD COLUMN request_key TEXT")
                    conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS operations_request_key_uq "
                        "ON operations(request_key) WHERE request_key IS NOT NULL"
                    )
                    conn.execute("PRAGMA user_version=2")
                else:
                    indexes = {
                        row["name"]: row
                        for row in conn.execute("PRAGMA index_list(operations)").fetchall()
                    }
                    request_index = indexes.get("operations_request_key_uq")
                    indexed_columns = [
                        row["name"]
                        for row in conn.execute(
                            "PRAGMA index_info(operations_request_key_uq)"
                        ).fetchall()
                    ]
                    if (
                        "request_key" not in operation_columns
                        or request_index is None
                        or not bool(request_index["unique"])
                        or not bool(request_index["partial"])
                        or indexed_columns != ["request_key"]
                    ):
                        raise DatabaseError("database v2 idempotency schema is invalid")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()
            self._secure_permissions()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
            self._secure_permissions()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def health(self) -> bool:
        try:
            with self.connection() as conn:
                return conn.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def create_submission(self, values: dict[str, Any], *, now: str) -> sqlite3.Row:
        columns = (
            "public_id", "title", "body", "refund_address", "refund_address_hint",
            "invoice_address", "invoice_diversifier_index", "zip321_uri", "memo",
            "amount_zat", "status", "demo", "created_at", "updated_at",
        )
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                f"INSERT INTO submissions ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(values.get(column) for column in columns[:-2]) + (now, now),
            )
            submission_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO state_events "
                "(submission_id,from_status,to_status,reason,metadata_json,created_at) "
                "VALUES (?,NULL,'awaiting_bond','submission_created',NULL,?)",
                (submission_id, now),
            )
            return conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()

    def get_submission(self, public_id: str) -> sqlite3.Row:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM submissions WHERE public_id=?", (public_id,)).fetchone()
        if row is None:
            raise NotFound("submission not found")
        return row

    def get_submission_by_id(self, submission_id: int, conn: sqlite3.Connection | None = None) -> sqlite3.Row:
        if conn is not None:
            row = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        else:
            with self.connection() as owned:
                row = owned.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if row is None:
            raise NotFound("submission not found")
        return row

    def list_submissions(
        self,
        *,
        status: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[sqlite3.Row], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if query:
            clauses.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend((f"%{escaped}%", f"%{escaped}%"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM submissions{where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM submissions{where} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return list(rows), int(total)

    @staticmethod
    def transition_in_connection(
        conn: sqlite3.Connection,
        submission_id: int,
        to_status: str,
        *,
        reason: str,
        now: str,
        metadata: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        if to_status not in STATUSES:
            raise ValueError("unknown state")
        current_row = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if current_row is None:
            raise NotFound("submission not found")
        current = current_row["status"]
        if current != to_status and to_status not in ALLOWED_TRANSITIONS[current]:
            raise StateConflict(current, to_status)

        updates = dict(updates or {})
        updates["updated_at"] = now
        if current != to_status:
            updates["status"] = to_status
        updates["version"] = int(current_row["version"]) + 1
        allowed_update_columns = {
            "status", "status_detail", "bond_txid", "bond_pool", "bond_output_index",
            "bond_confirmations", "bond_mined_height", "bond_tx_status", "mismatch_reason",
            "refund_operation_id", "refund_txid", "refund_confirmations", "refund_tx_status",
            "refund_error", "moderation_decision", "moderation_note", "moderated_at",
            "updated_at", "version",
        }
        if not set(updates) <= allowed_update_columns:
            raise ValueError("unsafe submission update column")
        assignments = ",".join(f"{column}=?" for column in updates)
        conn.execute(
            f"UPDATE submissions SET {assignments} WHERE id=?",
            (*updates.values(), submission_id),
        )
        if current != to_status:
            metadata_json = (
                json.dumps(metadata, separators=(",", ":"), sort_keys=True)
                if metadata
                else None
            )
            conn.execute(
                "INSERT INTO state_events "
                "(submission_id,from_status,to_status,reason,metadata_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (submission_id, current, to_status, reason[:120], metadata_json, now),
            )
        return conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()

    def transition(
        self,
        public_id: str,
        to_status: str,
        *,
        reason: str,
        now: str,
        metadata: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT id FROM submissions WHERE public_id=?", (public_id,)).fetchone()
            if row is None:
                raise NotFound("submission not found")
            return self.transition_in_connection(
                conn, row["id"], to_status, reason=reason, now=now, metadata=metadata, updates=updates
            )

    def timeline(self, submission_id: int) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT from_status,to_status,reason,metadata_json,created_at "
                    "FROM state_events WHERE submission_id=? ORDER BY id",
                    (submission_id,),
                ).fetchall()
            )

    def outputs_for_submission(self, submission_id: int) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM wallet_outputs WHERE submission_id=? ORDER BY id",
                    (submission_id,),
                ).fetchall()
            )

    def operations_for_submission(self, submission_id: int) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM operations WHERE submission_id=? ORDER BY created_at,operation_id",
                    (submission_id,),
                ).fetchall()
            )

    def active_operations(self) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM operations WHERE status IN ('queued','executing') ORDER BY created_at"
                ).fetchall()
            )

    def create_operation(
        self,
        *,
        operation_id: str,
        submission_id: int,
        kind: str,
        wallet_role: str,
        status: str,
        now: str,
        txid: str | None = None,
        broadcast: bool | None = None,
        request_key: str | None = None,
    ) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO operations "
                "(operation_id,request_key,submission_id,kind,wallet_role,status,txid,broadcast,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    operation_id, request_key, submission_id, kind, wallet_role, status, txid,
                    None if broadcast is None else int(broadcast), now, now,
                ),
            )

    def set_meta(self, key: str, value: str, *, now: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO metadata(key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_meta(self, key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def reset(self) -> int:
        with self.transaction(immediate=True) as conn:
            count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            conn.execute("DELETE FROM submissions")
            conn.execute("DELETE FROM metadata")
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('submissions','wallet_outputs','state_events')")
            except sqlite3.OperationalError:
                pass
        return int(count)
