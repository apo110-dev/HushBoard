from __future__ import annotations

import base64
import json
from pathlib import Path

from app.config import Settings
from app.wallet import ZalletAdapter


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.payload


def test_sendmany_wire_format_is_private_and_exact(tmp_path: Path):
    source = "utest1" + "q" * 104
    recipient = "utest1" + "p" * 104
    cookie = tmp_path / "operator.cookie"
    cookie.write_text("rpc-user:very-secret-cookie")
    participant_cookie = tmp_path / "participant.cookie"
    participant_cookie.write_text("rpc-user:participant-secret")
    wallets = tmp_path / "wallets.json"
    wallets.write_text(json.dumps({
        "wallets": {
            "operator": {"account_uuid": "operator-uuid", "address": source, "receiver_types": ["orchard"]},
            "participant": {"account_uuid": "participant-uuid", "address": source, "receiver_types": ["orchard"]},
        }
    }))
    settings = Settings.from_env({
        "HUSHBOARD_MODE": "live",
        "HUSHBOARD_DB": str(tmp_path / "db.sqlite"),
        "ADMIN_KEY": "long-enough-admin-key",
        "ZALLET_OPERATOR_COOKIE": str(cookie),
        "ZALLET_PARTICIPANT_COOKIE": str(participant_cookie),
        "ZALLET_WALLETS_FILE": str(wallets),
    }, root=tmp_path)

    calls = []
    def opener(request, timeout):
        body = json.loads(request.data)
        calls.append((request, body))
        if body["method"] == "z_listunifiedreceivers":
            result = {"orchard": "receiver"}
        elif body["method"] == "z_sendmany":
            result = "opid-00000000-0000-0000-0000-000000000001"
        else:
            raise AssertionError(body["method"])
        return Response({"jsonrpc": "2.0", "id": body["id"], "result": result, "error": None})

    adapter = ZalletAdapter(settings, opener=opener)
    launch = adapter.send_refund(recipient, "HB1:abcdefghijkl")
    assert launch.operation_id.startswith("opid-")

    request, send = calls[-1]
    assert send["method"] == "z_sendmany"
    params = send["params"]
    assert params[0] == source
    assert params[1] == [{
        "address": recipient,
        "amount": "0.01000000",
        "memo": b"HB1:abcdefghijkl".hex(),
    }]
    assert params[2:] == [1, None, "FullPrivacy"]
    assert base64.b64decode(request.headers["Authorization"].split()[1]).decode() == "rpc-user:very-secret-cookie"
    assert "very-secret-cookie" not in json.dumps(send)
