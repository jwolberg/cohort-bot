"""U3 tests: Ed25519 verification + /interactions contract."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app.discord import interactions as interactions_module
from app.discord import responses
from app.discord.interactions import get_public_key
from app.discord.verify import verify_signature
from app.main import create_app


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey.generate()


@pytest.fixture
def public_key_hex(signing_key: SigningKey) -> str:
    return signing_key.verify_key.encode().hex()


@pytest.fixture
def client(public_key_hex: str) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_public_key] = lambda: public_key_hex
    return TestClient(app)


def _sign(signing_key: SigningKey, timestamp: str, body: bytes) -> str:
    return signing_key.sign(timestamp.encode() + body).signature.hex()


def _post(client: TestClient, body: dict, *, signature: str, timestamp: str):
    raw = json.dumps(body).encode()
    return client.post(
        "/interactions",
        content=raw,
        headers={
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
            "Content-Type": "application/json",
        },
    )


# --- Execution-note test first: 401 on bad signature ---

def test_invalid_signature_returns_401(client: TestClient) -> None:
    resp = _post(
        client,
        {"type": 1},
        signature="00" * 64,  # not a valid signature for this body
        timestamp="1700000000",
    )
    assert resp.status_code == 401


def test_absent_signature_returns_401(client: TestClient) -> None:
    raw = json.dumps({"type": 1}).encode()
    resp = client.post("/interactions", content=raw)
    assert resp.status_code == 401


def test_valid_ping_returns_pong(client: TestClient, signing_key: SigningKey) -> None:
    ts = "1700000001"
    body = {"type": 1}
    raw = json.dumps(body).encode()
    resp = _post(client, body, signature=_sign(signing_key, ts, raw), timestamp=ts)
    assert resp.status_code == 200
    assert resp.json() == {"type": responses.PONG}


def test_tampered_body_returns_401(client: TestClient, signing_key: SigningKey) -> None:
    ts = "1700000002"
    signed_body = json.dumps({"type": 1}).encode()
    signature = _sign(signing_key, ts, signed_body)
    # Send a *different* body with the signature computed over the original.
    tampered = json.dumps({"type": 1, "x": "evil"}).encode()
    resp = client.post(
        "/interactions",
        content=tampered,
        headers={
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": ts,
        },
    )
    assert resp.status_code == 401


def test_command_routes_to_dispatcher(client: TestClient, signing_key: SigningKey) -> None:
    seen: dict = {}

    async def stub_dispatcher(interaction: dict) -> dict:
        seen["interaction"] = interaction
        return responses.message("dispatched", ephemeral=True)

    interactions_module.set_command_dispatcher(stub_dispatcher)
    try:
        ts = "1700000003"
        body = {"type": 2, "data": {"name": "help"}}
        raw = json.dumps(body).encode()
        resp = _post(client, body, signature=_sign(signing_key, ts, raw), timestamp=ts)
        assert resp.status_code == 200
        assert resp.json()["type"] == responses.CHANNEL_MESSAGE
        assert seen["interaction"]["data"]["name"] == "help"
    finally:
        interactions_module.set_command_dispatcher(None)  # type: ignore[arg-type]


# --- Response builders ---

def test_response_builders_emit_correct_type_codes() -> None:
    assert responses.pong() == {"type": 1}
    assert responses.message("hi")["type"] == 4
    assert responses.deferred()["type"] == 5
    assert responses.message("secret", ephemeral=True)["data"]["flags"] == 64
    assert responses.deferred(ephemeral=True)["data"]["flags"] == 64


def test_verify_signature_unit(signing_key: SigningKey, public_key_hex: str) -> None:
    ts = "123"
    body = b'{"type":1}'
    good = signing_key.sign(ts.encode() + body).signature.hex()
    assert verify_signature(public_key_hex, good, ts, body) is True
    assert verify_signature(public_key_hex, "zz", ts, body) is False  # bad hex
    assert verify_signature(public_key_hex, "00" * 64, ts, body) is False
