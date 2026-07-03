"""Register slash commands with Discord.

Usage:
    # Register against a test guild (instant propagation, for dev):
    uv run python -m scripts.register_commands --guild <GUILD_ID>

    # Register globally (production; ~1h propagation):
    uv run python -m scripts.register_commands

Reads the application id and bot token from settings. A bulk ``PUT`` overwrites
the full command set for the target scope.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.discord.commands import COMMANDS

API_BASE = "https://discord.com/api/v10"


class DiscordCreds(BaseSettings):
    """Only the two settings command registration actually needs.

    Registration is run from a dev machine that has no full ``.env`` (the app's
    other secrets live in Secret Manager), so it must not require them. Reads
    ``DISCORD_APP_ID`` / ``DISCORD_TOKEN`` from a ``.env`` file or the
    environment and ignores everything else.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    discord_app_id: str = Field(..., alias="DISCORD_APP_ID")
    discord_token: str = Field(..., alias="DISCORD_TOKEN")


def endpoint(app_id: str, guild_id: str | None) -> str:
    if guild_id:
        return f"{API_BASE}/applications/{app_id}/guilds/{guild_id}/commands"
    return f"{API_BASE}/applications/{app_id}/commands"


def register(
    commands: list[dict[str, Any]],
    *,
    app_id: str,
    token: str,
    guild_id: str | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """Bulk-overwrite the command set for the given scope."""
    url = endpoint(app_id, guild_id)
    headers = {"Authorization": f"Bot {token}"}
    owns_client = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        response = client.put(url, json=commands, headers=headers)
        response.raise_for_status()
        return response
    finally:
        if owns_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register Discord slash commands")
    parser.add_argument("--guild", help="Register against a single guild (dev)")
    args = parser.parse_args(argv)

    creds = DiscordCreds()
    response = register(
        COMMANDS,
        app_id=creds.discord_app_id,
        token=creds.discord_token,
        guild_id=args.guild,
    )
    scope = f"guild {args.guild}" if args.guild else "global"
    print(f"Registered {len(response.json())} commands ({scope}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
