"""Configuration for HushBoard's loopback-only backend.

No secret value is ever included in an API response.  Relative paths are resolved
against the repository root rather than the process working directory so that
``uvicorn app.main:app`` behaves consistently.
"""
from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(RuntimeError):
    """Raised when a security-sensitive configuration is unsafe."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse the deliberately small KEY=VALUE subset used by this project."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    except OSError as exc:
        raise ConfigurationError(f"cannot read environment file: {path}") from exc

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in "\"'" and value[-1:] == value[0]:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"invalid boolean configuration value: {value!r}")


def _int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise ConfigurationError("invalid integer configuration value") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"integer configuration must be in [{minimum}, {maximum}]")
    return parsed


def _float(value: str | None, default: float, *, minimum: float, maximum: float) -> float:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError("invalid numeric configuration value") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"numeric configuration must be in [{minimum}, {maximum}]")
    return parsed


def _path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _validate_rpc_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("Zallet RPC URL must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("Zallet RPC URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ConfigurationError("Zallet RPC must be loopback in this local-only prototype")
    return url.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    database_path: Path
    requested_mode: str
    network: str
    operator_url: str
    operator_cookie: Path
    participant_url: str
    participant_cookie: Path
    wallets_file: Path
    operator_account_name: str
    participant_account_name: str
    rpc_timeout: float
    minimum_confirmations: int
    bond_zat: int
    admin_key: str
    live_sends_enabled: bool
    demo_open_admin: bool
    watcher_interval: float
    explorer_tx_template: str
    cors_origin_regex: str

    @property
    def bond_zec(self) -> str:
        # Decimal protocol value derived with integer arithmetic; never round through float.
        whole, fractional = divmod(self.bond_zat, 100_000_000)
        return f"{whole}.{fractional:08d}"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        root: Path | None = None,
    ) -> Settings:
        root = (root or REPO_ROOT).resolve()
        file_values = _parse_env_file(root / ".env")
        supplied = dict(environ) if environ is not None else dict(os.environ)
        values = {**file_values, **supplied}

        requested_mode = values.get("HUSHBOARD_MODE", "auto").strip().lower()
        if requested_mode == "offline":
            requested_mode = "mock"
        if requested_mode not in {"auto", "live", "mock"}:
            raise ConfigurationError("HUSHBOARD_MODE must be auto, live, or mock")

        network = values.get("ZCASH_MODE", "testnet").strip().lower()
        if network in {"test", "testnet"}:
            network = "testnet"
        elif network in {"main", "mainnet"}:
            network = "mainnet"
        else:
            raise ConfigurationError("ZCASH_MODE must be testnet or mainnet")

        if network == "mainnet":
            raise ConfigurationError("mainnet is permanently disabled in this testnet prototype")

        operator_url = _validate_rpc_url(
            values.get("ZALLET_OPERATOR_URL", "http://127.0.0.1:41232")
        )
        participant_url = _validate_rpc_url(
            values.get("ZALLET_PARTICIPANT_URL", "http://127.0.0.1:41233")
        )

        bond_zat = _int(values.get("BOND_ZAT"), 1_000_000, minimum=1, maximum=21_000_000 * 100_000_000)
        # This is a protocol invariant, not a tuneable product price.
        if bond_zat != 1_000_000:
            raise ConfigurationError("HushBoard bonds must be exactly 1,000,000 zats")

        admin_key = values.get("ADMIN_KEY", "local-demo-only")
        if len(admin_key.encode("utf-8")) > 512:
            raise ConfigurationError("ADMIN_KEY is too long")
        if requested_mode == "live" and len(admin_key) < 12:
            raise ConfigurationError("live mode requires an ADMIN_KEY of at least 12 characters")

        explorer_default = (
            "https://blockexplorer.one/zcash/testnet/tx/{txid}"
            if network == "testnet"
            else "https://blockchair.com/zcash/transaction/{txid}"
        )
        explorer = values.get("ZCASH_EXPLORER_TX_URL", explorer_default).strip()
        parsed_explorer = urlparse(explorer.replace("{txid}", "0" * 64))
        if parsed_explorer.scheme not in {"http", "https"} or not parsed_explorer.netloc:
            raise ConfigurationError("ZCASH_EXPLORER_TX_URL must be an http(s) URL/template")

        return cls(
            root=root,
            database_path=_path(values.get("HUSHBOARD_DB", "./data/hushboard.db"), root),
            requested_mode=requested_mode,
            network=network,
            operator_url=operator_url,
            operator_cookie=_path(values.get("ZALLET_OPERATOR_COOKIE", ".runtime/operator.cookie"), root),
            participant_url=participant_url,
            participant_cookie=_path(values.get("ZALLET_PARTICIPANT_COOKIE", ".runtime/participant.cookie"), root),
            wallets_file=_path(values.get("ZALLET_WALLETS_FILE", ".runtime/wallets.json"), root),
            operator_account_name=values.get("ZALLET_OPERATOR_ACCOUNT", "hushboard-operator"),
            participant_account_name=values.get("ZALLET_PARTICIPANT_ACCOUNT", "hushboard-participant"),
            rpc_timeout=_float(values.get("ZALLET_RPC_TIMEOUT"), 15.0, minimum=0.25, maximum=120.0),
            minimum_confirmations=_int(values.get("ZCASH_MIN_CONFIRMATIONS"), 1, minimum=1, maximum=100),
            bond_zat=bond_zat,
            admin_key=admin_key,
            live_sends_enabled=_bool(values.get("HUSHBOARD_ENABLE_LIVE_SENDS"), False),
            demo_open_admin=_bool(values.get("HUSHBOARD_DEMO_OPEN_ADMIN"), True),
            watcher_interval=_float(values.get("HUSHBOARD_WATCH_INTERVAL"), 0.0, minimum=0.0, maximum=3600.0),
            explorer_tx_template=explorer,
            cors_origin_regex=values.get(
                "HUSHBOARD_CORS_ORIGIN_REGEX",
                r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?$",
            ),
        )
