"""Unit tests for GitHub App installation-token auth (U-GHAPP #5)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.github.app_auth import GitHubAppAuth, GitHubAppError
from app.github.client import GitHubClient

BASE = "https://api.github.com"


def _rsa_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


# Generated once — RSA keygen is the slow part of these tests.
_PEM = _rsa_pem()


def _decode_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


class _Clock:
    """Mutable injectable clock."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_app_jwt_carries_iss_and_bounded_expiry() -> None:
    clock = _Clock(1_000_000.0)
    auth = GitHubAppAuth("12345", _PEM, now=clock)

    token = auth._app_jwt()
    _, payload_b64, _ = token.split(".")
    payload = _decode_segment(payload_b64)

    assert payload["iss"] == "12345"
    assert payload["iat"] == 1_000_000 - 60  # backdated for clock skew
    assert payload["exp"] == 1_000_000 + 540
    # GitHub rejects App JWTs whose lifetime exceeds 10 minutes.
    assert payload["exp"] - payload["iat"] <= 600


def test_missing_app_id_or_key_raises() -> None:
    with pytest.raises(GitHubAppError):
        GitHubAppAuth("", _PEM)
    with pytest.raises(GitHubAppError):
        GitHubAppAuth("123", "")


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_mints_and_parses() -> None:
    route = respx.post(f"{BASE}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_installtoken", "expires_at": "2999-01-01T00:00:00Z"}
        )
    )
    auth = GitHubAppAuth("123", _PEM, now=_Clock(0.0))

    token = await auth.installation_token(42)

    assert token == "ghs_installtoken"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_cached_until_near_expiry() -> None:
    route = respx.post(f"{BASE}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_a", "expires_at": "1970-01-01T01:00:00Z"}
        )
    )
    clock = _Clock(0.0)  # token expires at t=3600
    auth = GitHubAppAuth("123", _PEM, now=clock)

    first = await auth.installation_token(42)
    clock.t = 100.0  # still well before expiry
    second = await auth.installation_token(42)

    assert first == second == "ghs_a"
    assert route.call_count == 1  # served from cache the second time


@respx.mock
@pytest.mark.asyncio
async def test_installation_token_refreshes_after_expiry() -> None:
    responses = iter(
        [
            httpx.Response(201, json={"token": "ghs_old", "expires_at": "1970-01-01T01:00:00Z"}),
            httpx.Response(201, json={"token": "ghs_new", "expires_at": "1970-01-01T02:00:00Z"}),
        ]
    )
    route = respx.post(f"{BASE}/app/installations/42/access_tokens").mock(
        side_effect=lambda request: next(responses)
    )
    clock = _Clock(0.0)
    auth = GitHubAppAuth("123", _PEM, now=clock)

    first = await auth.installation_token(42)
    clock.t = 3600.0  # at/after expiry (minus skew) → re-mint
    second = await auth.installation_token(42)

    assert first == "ghs_old"
    assert second == "ghs_new"
    assert route.call_count == 2


@respx.mock
@pytest.mark.asyncio
async def test_mint_failure_raises() -> None:
    respx.post(f"{BASE}/app/installations/99/access_tokens").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    auth = GitHubAppAuth("123", _PEM, now=_Clock(0.0))

    with pytest.raises(GitHubAppError):
        await auth.installation_token(99)


@respx.mock
@pytest.mark.asyncio
async def test_client_authenticates_with_installation_token() -> None:
    respx.post(f"{BASE}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_scoped", "expires_at": "2999-01-01T00:00:00Z"}
        )
    )
    commits_route = respx.get(f"{BASE}/repos/o/r/commits").mock(
        return_value=httpx.Response(200, json=[])
    )
    auth = GitHubAppAuth("123", _PEM, now=_Clock(0.0))

    async with GitHubClient(token_provider=auth.token_provider(42)) as gh:
        await gh.fetch_commits_since("o/r", None)

    sent = commits_route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer ghs_scoped"


def test_client_requires_some_credential() -> None:
    with pytest.raises(ValueError):
        GitHubClient()
