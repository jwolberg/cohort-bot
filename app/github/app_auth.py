"""GitHub App authentication — mint short-lived installation tokens.

A member's App *installation* is the consent grant (SPEC-GHAPP / ADR-0002). We
never persist a member token: we sign a short-lived **App JWT** with the App
private key (RS256) and exchange it for an **installation access token** (~1h
TTL, scoped to that installation's selected repos), minted on demand and cached
in-process until shortly before expiry.

Signing uses ``google.auth`` (already a project dependency) rather than adding
PyJWT — ``google.auth.jwt.encode`` produces exactly the RS256 JWT GitHub
expects, so no new dependency is introduced (CLAUDE.md §11).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from google.auth import crypt
from google.auth import jwt as google_jwt

from app.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://api.github.com"

# GitHub caps App-JWT lifetime at 10 min; use 9 to stay clear of clock skew.
_JWT_EXP_SECONDS = 540
# Backdate iat by a minute to tolerate skew between us and GitHub.
_JWT_IAT_SKEW_SECONDS = 60
# Re-mint an installation token once it's within this margin of expiry.
_REFRESH_SKEW_SECONDS = 60
# Fallback TTL if GitHub omits expires_at (it never should).
_DEFAULT_TOKEN_TTL_SECONDS = 3600


class GitHubAppError(Exception):
    """App-JWT signing or installation-token minting failed."""


@dataclass
class _CachedToken:
    token: str
    expires_at: float  # epoch seconds


def _parse_expiry(value: str | None, *, now: float) -> float:
    """Parse GitHub's ISO-8601 ``expires_at`` into epoch seconds."""
    if not value:
        return now + _DEFAULT_TOKEN_TTL_SECONDS
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class GitHubAppAuth:
    """Signs App JWTs and mints per-installation access tokens.

    ``now`` is injectable so tests can drive cache expiry deterministically.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        if not app_id or not private_key:
            raise GitHubAppError("GitHubAppAuth requires an app_id and private_key")
        self._app_id = str(app_id)
        self._signer = crypt.RSASigner.from_string(private_key)
        self._external_client = client
        self._client = client
        self._now = now
        self._cache: dict[str, _CachedToken] = {}

    # --- App JWT ---

    def _app_jwt(self) -> str:
        now = int(self._now())
        payload = {
            "iat": now - _JWT_IAT_SKEW_SECONDS,
            "exp": now + _JWT_EXP_SECONDS,
            "iss": self._app_id,
        }
        token = google_jwt.encode(self._signer, payload)
        return token.decode() if isinstance(token, bytes) else token

    # --- installation tokens ---

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=API_BASE, timeout=30.0)
        return self._client

    async def installation_token(self, installation_id: str | int) -> str:
        """Return a valid installation token, minting/refreshing as needed."""
        key = str(installation_id)
        cached = self._cache.get(key)
        if cached and cached.expires_at - self._now() > _REFRESH_SKEW_SECONDS:
            return cached.token
        token, expires_at = await self._mint(key)
        self._cache[key] = _CachedToken(token, expires_at)
        return token

    async def _mint(self, installation_id: str) -> tuple[str, float]:
        client = self._get_client()
        resp = await client.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self._app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if resp.status_code >= 300:
            logger.warning(
                "installation_token_mint_failed",
                extra={"installation_id": installation_id, "status": resp.status_code},
            )
            raise GitHubAppError(
                f"installation token mint failed ({resp.status_code}) for {installation_id}"
            )
        data: dict[str, Any] = resp.json()
        token = data.get("token")
        if not token:
            raise GitHubAppError(
                f"installation token response missing token for {installation_id}"
            )
        expires_at = _parse_expiry(data.get("expires_at"), now=self._now())
        return token, expires_at

    def token_provider(self, installation_id: str | int) -> Callable[[], Awaitable[str]]:
        """A zero-arg async provider yielding a fresh token for one installation.

        Pass to ``GitHubClient(token_provider=...)`` so the client authenticates
        as that installation without ever holding a static secret.
        """

        async def _provider() -> str:
            return await self.installation_token(installation_id)

        return _provider

    async def aclose(self) -> None:
        if self._client is not None and self._external_client is None:
            await self._client.aclose()

    async def __aenter__(self) -> "GitHubAppAuth":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
