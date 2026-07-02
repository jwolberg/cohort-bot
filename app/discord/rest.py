"""Discord REST calls: posting channel messages and editing follow-ups.

Two surfaces:
- ``edit_original_response`` PATCHes the interaction follow-up webhook (used by
  the slow-command worker; auth is the interaction token in the URL, no bot
  token needed).
- ``post_channel_message`` posts to a channel with the bot token (used by the
  daily digest in U9).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://discord.com/api/v10"


class DiscordREST:
    def __init__(
        self,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        api_base: str = API_BASE,
    ) -> None:
        self._token = token
        self._client = client
        self._api_base = api_base

    @classmethod
    def from_settings(cls, settings: Settings, *, client: httpx.AsyncClient | None = None) -> "DiscordREST":
        return cls(settings.discord_token, client=client)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    @staticmethod
    def _body(embeds: list[dict[str, Any]] | None, content: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if content is not None:
            body["content"] = content
        if embeds:
            body["embeds"] = embeds
        return body

    async def edit_original_response(
        self,
        application_id: str,
        interaction_token: str,
        *,
        embeds: list[dict[str, Any]] | None = None,
        content: str | None = None,
    ) -> None:
        """PATCH the deferred interaction's original response with final content."""
        url = f"{self._api_base}/webhooks/{application_id}/{interaction_token}/messages/@original"
        response = await self._get_client().patch(url, json=self._body(embeds, content))
        response.raise_for_status()

    async def post_channel_message(
        self,
        channel_id: str,
        *,
        embeds: list[dict[str, Any]] | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Post a message to a channel using the bot token."""
        url = f"{self._api_base}/channels/{channel_id}/messages"
        response = await self._get_client().post(
            url,
            json=self._body(embeds, content),
            headers={"Authorization": f"Bot {self._token}"},
        )
        response.raise_for_status()
        return response.json()
