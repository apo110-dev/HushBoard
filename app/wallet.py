"""Small, auditable Zallet JSON-RPC adapter built on :mod:`urllib`.

The adapter deliberately owns all wallet-specific wire formats: cookie Basic
authentication, string decimal amounts, hexadecimal memos, a null fee, the
``FullPrivacy`` policy, and asynchronous operation inspection.
"""
from __future__ import annotations

import base64
import itertools
import json
import re
import secrets
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Settings

_METHOD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TXID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_MAX_RPC_RESPONSE = 8 * 1024 * 1024
_BECH32_DATA = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_TESTNET_UA_RE = re.compile(r"(?i)\butest1[023456789acdefghjklmnpqrstuvwxyz]{20,512}\b")


class WalletError(RuntimeError):
    """Base class for errors safe to surface after sanitization."""


class WalletUnavailable(WalletError):
    pass


class WalletRpcError(WalletError):
    def __init__(self, method: str, code: int | None, message: str):
        self.method = method
        self.code = code
        self.rpc_message = _safe_text(message)
        super().__init__(f"Zallet {method} failed: {self.rpc_message}")


class WalletValidationError(WalletError):
    pass


def _safe_text(value: object, maximum: int = 300) -> str:
    text = str(value)
    text = " ".join(text.replace("\x00", "").split())
    return text[:maximum] or "unspecified wallet error"


def public_wallet_error(error: WalletError) -> str:
    """Return a bounded browser-safe message without RPC bodies or wallet identifiers."""
    if isinstance(error, WalletRpcError):
        code = f" (code {error.code})" if error.code is not None else ""
        return f"wallet RPC {error.method} failed{code}"
    if isinstance(error, WalletValidationError):
        return _TESTNET_UA_RE.sub("<redacted testnet UA>", _safe_text(error))
    return "wallet service is unavailable"


def _read_cookie(path: Path) -> str:
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size <= 2 or stat.st_size > 4096:
            raise WalletUnavailable("wallet cookie file is missing or invalid")
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError, UnicodeError) as exc:
        raise WalletUnavailable("wallet cookie is unavailable") from exc
    if ":" not in value or any(ch in value for ch in "\r\n\x00"):
        raise WalletUnavailable("wallet cookie format is invalid")
    return value


class JsonRpcClient:
    """Thread-safe JSON-RPC 2.0 client with cookie Basic authentication."""

    def __init__(
        self,
        url: str,
        cookie_path: Path,
        *,
        timeout: float,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.url = url
        self.cookie_path = cookie_path
        self.timeout = timeout
        self._opener = opener
        self._ids = itertools.count(1)
        self._id_lock = threading.Lock()

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        if not _METHOD_RE.fullmatch(method):
            raise ValueError("invalid RPC method name")
        with self._id_lock:
            request_id = next(self._ids)
        cookie = _read_cookie(self.cookie_path)
        auth = base64.b64encode(cookie.encode("utf-8")).decode("ascii")
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or []},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "HushBoard/1.0 (loopback Zallet adapter)",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RPC_RESPONSE + 1)
        except HTTPError as exc:
            # Do not echo response bodies: proxies occasionally include request headers.
            raise WalletUnavailable(f"wallet RPC returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WalletUnavailable("wallet RPC is unavailable") from exc
        finally:
            # Minimise how long the immutable Python string remains referenced here.
            del cookie

        if len(raw) > _MAX_RPC_RESPONSE:
            raise WalletUnavailable("wallet RPC response exceeded the safety limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WalletUnavailable("wallet RPC returned invalid JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("id") not in {request_id, str(request_id)}:
            raise WalletUnavailable("wallet RPC returned an invalid response envelope")
        error = decoded.get("error")
        if error:
            if isinstance(error, dict):
                code = error.get("code") if isinstance(error.get("code"), int) else None
                message = error.get("message", "unspecified RPC error")
            else:
                code, message = None, error
            raise WalletRpcError(method, code, str(message))
        if "result" not in decoded:
            raise WalletUnavailable("wallet RPC response omitted its result")
        return decoded["result"]


@dataclass(frozen=True, slots=True)
class WalletIdentity:
    role: str
    account_uuid: str
    address: str


@dataclass(frozen=True, slots=True)
class DerivedAddress:
    address: str
    diversifier_index: str | None
    receiver_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationLaunch:
    operation_id: str
    wallet_role: str


class ZalletAdapter:
    """The two-wallet HushBoard view of Zallet."""

    def __init__(self, settings: Settings, *, opener: Callable[..., Any] = urlopen) -> None:
        self.settings = settings
        self.operator = JsonRpcClient(
            settings.operator_url,
            settings.operator_cookie,
            timeout=settings.rpc_timeout,
            opener=opener,
        )
        self.participant = JsonRpcClient(
            settings.participant_url,
            settings.participant_cookie,
            timeout=settings.rpc_timeout,
            opener=opener,
        )
        self._identity_lock = threading.Lock()
        self._identities: dict[str, WalletIdentity] = {}

    def client(self, role: str) -> JsonRpcClient:
        if role == "operator":
            return self.operator
        if role == "participant":
            return self.participant
        raise ValueError("wallet role must be operator or participant")

    def probe(self, role: str) -> dict[str, Any]:
        status = self.client(role).call("getwalletstatus", [])
        if not isinstance(status, dict):
            raise WalletUnavailable("wallet status response is invalid")
        return status

    def _identity_from_file(self, role: str) -> WalletIdentity | None:
        try:
            if self.settings.wallets_file.stat().st_size > 128 * 1024:
                return None
            payload = json.loads(self.settings.wallets_file.read_text(encoding="utf-8"))
            record = payload.get("wallets", {}).get(role, {})
            account_uuid = record.get("account_uuid")
            address = record.get("address")
            receivers = record.get("receiver_types")
            if (
                isinstance(account_uuid, str)
                and isinstance(address, str)
                and receivers == ["orchard"]
                and address.startswith("utest1")
            ):
                return WalletIdentity(role, account_uuid, address)
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError):
            return None
        return None

    def _discover_identity(self, role: str) -> WalletIdentity:
        client = self.client(role)
        account_name = (
            self.settings.operator_account_name if role == "operator" else self.settings.participant_account_name
        )
        accounts = client.call("z_listaccounts", [True])
        if not isinstance(accounts, list):
            raise WalletUnavailable("wallet account list response is invalid")
        account = next(
            (item for item in accounts if isinstance(item, dict) and item.get("name") == account_name),
            None,
        )
        if not account or not isinstance(account.get("account_uuid"), str):
            raise WalletUnavailable(f"{role} wallet account is not initialized")
        account_uuid = account["account_uuid"]
        # A newly-derived Orchard-only UA is a safe account selector for z_sendmany.
        derived = client.call("z_getaddressforaccount", [account_uuid, ["orchard"]])
        parsed = self._parse_derived_address(derived)
        return WalletIdentity(role, account_uuid, parsed.address)

    def identity(self, role: str) -> WalletIdentity:
        with self._identity_lock:
            cached = self._identities.get(role)
            if cached:
                return cached
            identity = self._identity_from_file(role) or self._discover_identity(role)
            # Verify that the source is shielded-only instead of trusting a stale file.
            receivers = self.client(role).call("z_listunifiedreceivers", [identity.address])
            if not isinstance(receivers, dict) or set(receivers) != {"orchard"}:
                raise WalletValidationError(f"{role} source UA is not Orchard-only")
            self._identities[role] = identity
            return identity

    @staticmethod
    def _parse_derived_address(result: Any) -> DerivedAddress:
        if not isinstance(result, dict):
            raise WalletUnavailable("address derivation response is invalid")
        address = result.get("address")
        receiver_types = result.get("receiver_types")
        index = result.get("diversifier_index")
        if (
            not isinstance(address, str)
            or not address.startswith("utest1")
            or receiver_types != ["orchard"]
        ):
            raise WalletValidationError("Zallet did not derive an Orchard-only testnet UA")
        return DerivedAddress(
            address=address,
            diversifier_index=str(index) if isinstance(index, (str, int)) else None,
            receiver_types=("orchard",),
        )

    def derive_invoice_address(self) -> DerivedAddress:
        identity = self.identity("operator")
        # Omitting the index asks Zallet for the next available shielded address. The DB's
        # unique constraint is a second line of defence against accidental reuse.
        result = self.operator.call(
            "z_getaddressforaccount", [identity.account_uuid, ["orchard"]]
        )
        derived = self._parse_derived_address(result)
        receivers = self.operator.call("z_listunifiedreceivers", [derived.address])
        if not isinstance(receivers, dict) or set(receivers) != {"orchard"}:
            raise WalletValidationError("invoice UA contains a non-Orchard receiver")
        return derived

    def validate_refund_address(self, address: str) -> None:
        receivers = self.operator.call("z_listunifiedreceivers", [address])
        if not isinstance(receivers, dict) or "orchard" not in receivers:
            raise WalletValidationError("refund UA must contain an Orchard receiver")

    def send_bond(self, invoice_address: str, memo: str) -> OperationLaunch:
        return self._send("participant", invoice_address, memo)

    def send_refund(self, refund_address: str, memo: str) -> OperationLaunch:
        return self._send("operator", refund_address, memo)

    def _send(self, role: str, recipient: str, memo: str) -> OperationLaunch:
        identity = self.identity(role)
        # Amount remains a JSON string all the way to urllib/json.dumps. This avoids
        # binary floating-point and is accepted by Zallet's zcashd-compatible parser.
        recipient_spec = {
            "address": recipient,
            "amount": self.settings.bond_zec,
            "memo": memo.encode("utf-8").hex(),
        }
        result = self.client(role).call(
            "z_sendmany",
            [
                identity.address,
                [recipient_spec],
                self.settings.minimum_confirmations,
                None,                 # Zallet/ZIP-317 calculates the fee.
                "FullPrivacy",
            ],
        )
        operation_id = result.get("operationid") if isinstance(result, dict) else result
        if not isinstance(operation_id, str) or not operation_id.startswith("opid-"):
            raise WalletUnavailable("z_sendmany did not return an operation id")
        return OperationLaunch(operation_id=operation_id, wallet_role=role)

    def operation_statuses(self, role: str, operation_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(operation_ids))
        if not ids:
            return []
        result = self.client(role).call("z_getoperationstatus", [ids])
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise WalletUnavailable("operation status response is invalid")
        return result

    def list_operator_transactions(self, *, page_size: int = 500, max_pages: int = 100) -> list[dict[str, Any]]:
        identity = self.identity("operator")
        transactions: list[dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            # end_height=null is intentional: it includes mempool transactions.
            page = self.operator.call(
                "z_listtransactions", [identity.account_uuid, None, None, offset, page_size]
            )
            if not isinstance(page, list) or not all(isinstance(tx, dict) for tx in page):
                raise WalletUnavailable("transaction list response is invalid")
            transactions.extend(page)
            if len(page) < page_size:
                return transactions
            offset += len(page)
        raise WalletUnavailable("transaction history exceeded the configured scan bound")

    def view_transaction(self, role: str, txid: str) -> dict[str, Any]:
        if not _TXID_RE.fullmatch(txid):
            raise WalletValidationError("invalid transaction id")
        result = self.client(role).call("z_viewtransaction", [txid])
        if not isinstance(result, dict):
            raise WalletUnavailable("transaction detail response is invalid")
        return result


def mock_unified_address() -> str:
    """Return a visibly testnet-shaped placeholder; it is never presented as live."""
    return "utest1" + "".join(secrets.choice(_BECH32_DATA) for _ in range(104))


def mock_txid() -> str:
    return secrets.token_hex(32)


def mock_operation_id() -> str:
    # Zallet uses opid-UUID; keeping the same shape exercises the operation UI.
    raw = secrets.token_hex(16)
    return f"opid-{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def extract_operation_result(result: Any) -> tuple[str | None, list[str], bool | None]:
    """Normalize both beta and newer Zallet operation result shapes."""
    if isinstance(result, str) and _TXID_RE.fullmatch(result):
        return result.lower(), [result.lower()], None
    if not isinstance(result, dict):
        return None, [], None
    txid = result.get("txid")
    txids = result.get("txids")
    normalized: list[str] = []
    if isinstance(txids, list):
        normalized.extend(x.lower() for x in txids if isinstance(x, str) and _TXID_RE.fullmatch(x))
    if isinstance(txid, str) and _TXID_RE.fullmatch(txid):
        txid = txid.lower()
        if txid not in normalized:
            normalized.insert(0, txid)
    else:
        txid = normalized[0] if len(normalized) == 1 else None
    broadcast = result.get("broadcast") if isinstance(result.get("broadcast"), bool) else None
    return txid, normalized, broadcast
