from __future__ import annotations

import base64
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.service import HushBoardService
from app.wallet import WalletRpcError


class _LeakyValidationWallet:
    def validate_refund_address(self, address: str):
        raise WalletRpcError("z_validateaddress", -5, f"Invalid address: {address}")


def test_wallet_rpc_errors_never_reflect_a_full_refund_ua(settings, refund_address):
    live_settings = replace(settings, requested_mode="live")
    api = create_app(
        live_settings,
        service=HushBoardService(live_settings, wallet=_LeakyValidationWallet()),
    )
    with TestClient(api) as client:
        response = client.post(
            "/api/submissions",
            json={
                "title": "Redaction",
                "body": "Do not echo the refund address.",
                "refund_address": refund_address,
            },
        )
    assert response.status_code == 422
    assert refund_address not in response.text
    assert "z_validateaddress" in response.text

def test_spa_api_contract_and_open_loopback_demo_admin(settings, refund_address):
    service = HushBoardService(settings)
    api = create_app(settings, service=service)
    with TestClient(api) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        health_json = health.json()
        assert health_json["mode"] == "mock"
        assert health_json["admin_required"] is False
        assert settings.admin_key not in health.text

        invalid = client.post("/api/submissions", json={
            "title": "x", "body": "y", "refund_address": "u1bad"
        })
        assert invalid.status_code == 422

        response = client.post("/api/submissions", json={
            "title": "  Better   labels  ",
            "body": "The filter labels need more contrast.",
            "refund_address": refund_address,
        })
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["title"] == "Better labels"
        assert created["invoice"]["qr_svg"].startswith("data:image/svg+xml;base64,")
        svg = base64.b64decode(created["invoice"]["qr_svg"].split(",", 1)[1])
        assert b"<svg" in svg

        public_id = created["id"]
        paid = client.post(f"/api/submissions/{public_id}/pay", json={})
        assert paid.status_code == 200
        assert paid.json()["submission"]["status"] == "bond_pending"
        assert client.post("/api/sync").status_code == 200

        # TestClient is treated like a loopback caller only for the explicitly open
        # offline demo; no admin key is embedded in the response or SPA.
        moderated = client.post(
            f"/api/submissions/{public_id}/moderate",
            json={"decision": "keep", "note": "accepted"},
        )
        assert moderated.status_code == 200, moderated.text
        assert moderated.json()["submission"]["status"] == "kept"

        listing = client.get("/api/submissions?status=kept&limit=10&offset=0")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["refund_address_hint"]
        assert refund_address not in listing.text

        reset = client.post("/api/demo/reset")
        assert reset.status_code == 200
        assert reset.json()["deleted"] == 1


def test_unknown_status_and_unknown_submission_are_bounded(settings):
    api = create_app(settings, service=HushBoardService(settings))
    with TestClient(api) as client:
        assert client.get("/api/submissions?status=nope").status_code == 422
        assert client.get("/api/submissions/not-valid").status_code == 404
        oversized = {"title": "x", "body": "z" * 5000, "refund_address": "utest1" + "q" * 104}
        result = client.post("/api/submissions", json=oversized)
        assert result.status_code == 422
        assert "z" * 100 not in result.text



def test_repository_static_spa_is_served_at_root(settings):
    from dataclasses import replace
    from pathlib import Path

    repository_root = Path(__file__).resolve().parents[2]
    settings = replace(settings, root=repository_root)
    api = create_app(settings, service=HushBoardService(settings))
    with TestClient(api) as client:
        index = client.get("/")
        asset = client.get("/static/app.js")
        assert index.status_code == 200
        assert index.headers["content-type"].startswith("text/html")
        assert b"HushBoard" in index.content
        assert asset.status_code == 200
        assert b"/submissions" in asset.content
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/openapi.json").status_code == 200
        assert client.get("/api/docs").status_code == 404

def test_remote_client_is_rejected_even_with_a_loopback_host_header(settings):
    api = create_app(settings, service=HushBoardService(settings))
    with TestClient(
        api,
        base_url="http://localhost",
        client=("198.51.100.42", 50000),
    ) as client:
        response = client.get("/api/health", headers={"Host": "localhost"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "loopback_only"
