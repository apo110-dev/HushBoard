#!/usr/bin/env python3
"""Create the two disposable testnet accounts and shielded-only UAs idempotently."""
from __future__ import annotations

import base64
import json
import os
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"

WALLETS = {
    "operator": ("http://127.0.0.1:41232", RUNTIME / "operator.cookie", "hushboard-operator"),
    "participant": ("http://127.0.0.1:41233", RUNTIME / "participant.cookie", "hushboard-participant"),
}

def rpc(url: str, cookie_path: pathlib.Path, method: str, params: list | None = None):
    cookie = cookie_path.read_text().strip()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(cookie.encode()).decode(),
    })
    with urllib.request.urlopen(req, timeout=120) as res:
        payload = json.load(res)
    if payload.get("error"):
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload["result"]

existing = {}
wallets_path = RUNTIME / "wallets.json"
if wallets_path.exists():
    try:
        existing = json.loads(wallets_path.read_text()).get("wallets", {})
    except (OSError, ValueError):
        existing = {}

output = {"network": "testnet", "wallets": {}}
for key, (url, cookie_path, account_name) in WALLETS.items():
    accounts = rpc(url, cookie_path, "z_listaccounts", [True])
    account = next((a for a in accounts if a.get("name") == account_name), None)
    if account is None:
        created = rpc(url, cookie_path, "z_getnewaccount", [account_name])
        account_uuid = created["account_uuid"]
    else:
        account_uuid = account["account_uuid"]

    previous = existing.get(key, {})
    previous_address = previous.get("address") if previous.get("account_uuid") == account_uuid else None
    if previous_address:
        receivers = rpc(url, cookie_path, "z_listunifiedreceivers", [previous_address])
        if set(receivers) == {"orchard"}:
            shielded_address = previous_address
        else:
            previous_address = None
    if not previous_address:
        derived = rpc(url, cookie_path, "z_getaddressforaccount", [account_uuid, ["orchard"]])
        shielded_address = derived["address"]
    output["wallets"][key] = {
        "account_uuid": account_uuid,
        "address": shielded_address,
        "receiver_types": ["orchard"],
    }

path = RUNTIME / "wallets.json"
path.write_text(json.dumps(output, indent=2) + "\n")
os.chmod(path, 0o600)
for key, wallet in output["wallets"].items():
    addr = wallet["address"]
    print(f"[hushboard] {key}: {addr[:22]}…{addr[-10:]}")
