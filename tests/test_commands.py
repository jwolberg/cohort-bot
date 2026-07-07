"""U4 tests: command schemas + registration script."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.discord import commands
from scripts.register_commands import DiscordCreds, endpoint, register


def test_command_names_match_expected() -> None:
    names = {c["name"] for c in commands.COMMANDS}
    assert names == {"track", "repo", "branches", "user", "digest", "substack", "publication", "help"}


def test_substack_declares_optional_window_with_choices() -> None:
    opt = commands.SUBSTACK_COMMAND["options"][0]
    assert opt["name"] == "window"
    assert opt["type"] == commands.STRING
    assert opt["required"] is False
    assert {c["value"] for c in opt["choices"]} == {"1d", "7d", "30d"}


def test_all_commands_are_chat_input() -> None:
    assert all(c["type"] == commands.CHAT_INPUT for c in commands.COMMANDS)


def test_track_has_three_subcommands_with_required_string_options() -> None:
    subs = {opt["name"]: opt for opt in commands.TRACK_COMMAND["options"]}
    assert set(subs) == {"add", "remove", "list"}
    assert all(opt["type"] == commands.SUB_COMMAND for opt in subs.values())

    # add/remove take a required string username; list takes none.
    for name in ("add", "remove"):
        opts = subs[name]["options"]
        assert len(opts) == 1
        assert opts[0]["type"] == commands.STRING
        assert opts[0]["required"] is True
    assert "options" not in subs["list"]


def test_repo_and_branches_declare_repo_option() -> None:
    for cmd in (commands.REPO_COMMAND, commands.BRANCHES_COMMAND):
        opt = cmd["options"][0]
        assert opt["name"] == "repo"
        assert opt["type"] == commands.STRING
        assert opt["required"] is True


def test_user_declares_username_option() -> None:
    opt = commands.USER_COMMAND["options"][0]
    assert opt["name"] == "username"
    assert opt["type"] == commands.STRING


def test_digest_declares_today_and_yesterday_subcommands() -> None:
    subs = {opt["name"]: opt for opt in commands.DIGEST_COMMAND["options"]}
    assert set(subs) == {"today", "yesterday"}
    assert all(opt["type"] == commands.SUB_COMMAND for opt in subs.values())


def test_help_has_no_options() -> None:
    assert "options" not in commands.HELP_COMMAND


def test_discord_creds_only_requires_discord_fields(monkeypatch) -> None:
    # Registration must work without the app's other secrets (Secret Manager).
    for var in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY", "GCP_PROJECT", "DISCORD_PUBLIC_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISCORD_APP_ID", "app123")
    monkeypatch.setenv("DISCORD_TOKEN", "bot-tok")
    creds = DiscordCreds()
    assert creds.discord_app_id == "app123"
    assert creds.discord_token == "bot-tok"


def test_endpoint_guild_vs_global() -> None:
    assert endpoint("app123", "guild9").endswith("/applications/app123/guilds/guild9/commands")
    assert endpoint("app123", None).endswith("/applications/app123/commands")


@respx.mock
def test_register_puts_to_guild_endpoint_with_payload() -> None:
    url = endpoint("app123", "guild9")
    route = respx.put(url).mock(
        return_value=httpx.Response(200, json=commands.COMMANDS)
    )
    resp = register(
        commands.COMMANDS, app_id="app123", token="tok", guild_id="guild9"
    )
    assert resp.status_code == 200
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bot tok"
    import json as _json

    sent = _json.loads(request.content)
    assert {c["name"] for c in sent} == {
        "track", "repo", "branches", "user", "digest", "substack", "publication", "help"
    }


@respx.mock
def test_register_global_endpoint_when_no_guild() -> None:
    url = endpoint("app123", None)
    route = respx.put(url).mock(return_value=httpx.Response(200, json=[]))
    register(commands.COMMANDS, app_id="app123", token="tok")
    assert route.called
