"""Slash command schema definitions (Discord application commands).

These are declared once and registered out of band via
``scripts/register_commands.py`` (ARCHITECTURE §5A). Option types used:
- 1 SUB_COMMAND
- 3 STRING

Command surface (PRD "Slash Commands"):
- /track add|remove|list
- /repo <owner/repo>
- /branches <owner/repo>
- /user <github_user>
- /digest today|yesterday
- /substack [1d|7d|30d]
- /publication <url_or_host>
- /help
"""

from __future__ import annotations

from typing import Any

# Application command type.
CHAT_INPUT = 1

# Option types.
SUB_COMMAND = 1
STRING = 3


def _string_option(name: str, description: str, *, required: bool = True) -> dict[str, Any]:
    return {
        "type": STRING,
        "name": name,
        "description": description,
        "required": required,
    }


def _subcommand(name: str, description: str, options: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    sub: dict[str, Any] = {"type": SUB_COMMAND, "name": name, "description": description}
    if options:
        sub["options"] = options
    return sub


TRACK_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "track",
    "description": "Manage tracked GitHub users",
    "options": [
        _subcommand(
            "add",
            "Track a GitHub user (admin only)",
            [_string_option("username", "GitHub username to track")],
        ),
        _subcommand(
            "remove",
            "Stop tracking a GitHub user (admin only)",
            [_string_option("username", "GitHub username to stop tracking")],
        ),
        _subcommand("list", "List tracked GitHub users"),
    ],
}

REPO_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "repo",
    "description": "Inspect a GitHub repository",
    "options": [_string_option("repo", "Repository as owner/repo")],
}

BRANCHES_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "branches",
    "description": "List branches of a GitHub repository",
    "options": [_string_option("repo", "Repository as owner/repo")],
}

USER_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "user",
    "description": "Show recent activity for a GitHub user",
    "options": [_string_option("username", "GitHub username")],
}

DIGEST_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "digest",
    "description": "Post the engineering activity digest",
    "options": [
        _subcommand("today", "Digest for today"),
        _subcommand("yesterday", "Digest for yesterday"),
    ],
}

SUBSTACK_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "substack",
    "description": "Show recent posts from tracked Substack publications",
    "options": [
        {
            "type": STRING,
            "name": "window",
            "description": "Time window (default: last 1 day)",
            "required": False,
            "choices": [
                {"name": "Last 1 day", "value": "1d"},
                {"name": "Last 7 days", "value": "7d"},
                {"name": "Last 30 days", "value": "30d"},
            ],
        }
    ],
}

PUBLICATION_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "publication",
    "description": "Inspect a Substack publication",
    "options": [_string_option("publication", "Publication URL or host, e.g. pragmaticengineer.substack.com")],
}

HELP_COMMAND: dict[str, Any] = {
    "type": CHAT_INPUT,
    "name": "help",
    "description": "Show the command reference",
}

COMMANDS: list[dict[str, Any]] = [
    TRACK_COMMAND,
    REPO_COMMAND,
    BRANCHES_COMMAND,
    USER_COMMAND,
    DIGEST_COMMAND,
    SUBSTACK_COMMAND,
    PUBLICATION_COMMAND,
    HELP_COMMAND,
]
