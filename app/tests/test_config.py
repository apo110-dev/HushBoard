from __future__ import annotations

import pytest

from app.__main__ import _loopback_host, _port
from app.config import ConfigurationError, Settings


def test_remote_wallet_rpc_is_rejected_by_default(tmp_path):
    with pytest.raises(ConfigurationError, match="loopback"):
        Settings.from_env({
            "HUSHBOARD_MODE": "mock",
            "HUSHBOARD_DB": str(tmp_path / "db"),
            "ZALLET_OPERATOR_URL": "http://wallet.example:1234",
        }, root=tmp_path)


def test_bond_amount_is_a_protocol_invariant(tmp_path):
    with pytest.raises(ConfigurationError, match="exactly"):
        Settings.from_env({
            "HUSHBOARD_MODE": "mock",
            "HUSHBOARD_DB": str(tmp_path / "db"),
            "BOND_ZAT": "999999",
        }, root=tmp_path)


def test_module_entrypoint_rejects_remote_bind_and_bad_ports():
    assert _loopback_host("127.0.0.1") == "127.0.0.1"
    assert _loopback_host("::1") == "::1"
    assert _port("4173") == 4173
    with pytest.raises(SystemExit, match="loopback"):
        _loopback_host("0.0.0.0")
    with pytest.raises(SystemExit, match="1..65535"):
        _port("70000")
