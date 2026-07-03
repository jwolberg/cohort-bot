# PRD: GitHub Activity Digest Discord Bot

## Overview

A Discord bot that monitors selected GitHub users and produces a daily engineering activity digest. The bot helps engineering managers, founders, recruiters, and technical communities stay informed about open-source work without manually checking GitHub.

The bot summarizes who was active, where they were active, and provides on-demand exploration of repositories through Discord slash commands.

## Problem

Following dozens of GitHub developers requires repeatedly checking profiles and repositories.

Questions users frequently have include:

- What has everyone been working on today?
- Which repositories are active?
- Who is shipping the most?
- What changed in a particular repository?
- Which branches are currently active?

GitHub provides the raw information but not a concise daily engineering digest.

## Goals

- Daily GitHub activity summaries
- Easy management of tracked GitHub users
- Interactive repository exploration from Discord
- Low operational overhead
- Extensible architecture for additional content sources

## Target Users

- Engineering managers
- Startup founders
- Open-source communities
- Recruiters
- Developer Discord communities
- Individual developers tracking peers

## MVP Features

### User Tracking

Administrators can manage tracked GitHub usernames.

**Slash Commands**

- `/track add <github_username>`
- `/track remove <github_username>`
- `/track list`

The bot stores tracked usernames in persistent storage.

### Daily Digest

Once per day, the bot posts a summary to a configured Discord channel.

Example:

```
GitHub Daily Digest
July 2

8 developers tracked

Jay
12 commits

TradingVolatility
• 7 commits
• Added IV surface endpoint
• Refactored GEX cache

GlobalPulse
• 5 commits
• Added entity extraction pipeline

--------------------------------

Sarah
4 commits

DiscordBot
• 4 commits
• Added slash command support
```

For each user:

- total commits
- repositories modified
- commits per repository
- repository description
- latest commit messages
- AI-generated summary of work performed

### Repository Inspection

Users can inspect any tracked repository.

`/repo <owner>/<repo>`

Returns:

- description
- language
- stars
- forks
- default branch
- latest activity
- recent commits
- contributors

### Branch Inspection

`/branches <owner>/<repo>`

Displays:

- branch names
- latest commit
- last updated time
- author
- ahead/behind default branch (if available)

### Activity Cache

The bot stores:

- last polling timestamp
- processed commit SHAs

This prevents duplicate reporting.

## Architecture

```
Discord (Hikari)
        │
Slash Commands
Scheduler
        │
GitHub Client
        │
GitHub REST API
        │
Activity Database
        │
LLM Summarizer
        │
Discord Messages
```

### Components

#### Discord Bot

Responsibilities

- slash commands
- scheduled jobs
- embeds
- permissions

#### GitHub Client

Responsibilities

- fetch users
- fetch repositories
- fetch commits
- fetch branches

#### Storage

SQLite initially.

Tables

**TrackedUsers**

- username
- enabled
- created_at

**ProcessedCommits**

- repo
- sha
- timestamp

**RepositoryCache**

- repo
- description
- language
- stars
- updated_at

#### Summarizer

Input

- commit messages
- repository description
- commit count

Output

A concise paragraph describing the day's engineering work.

Example:

> Most work focused on backend infrastructure, adding new API endpoints and improving caching performance across the analytics platform.

## Slash Commands

### Tracking

- `/track add`
- `/track remove`
- `/track list`

### Activity

- `/digest today`
- `/digest yesterday`
- `/user <github_user>`
- `/substack [1d|7d|30d]` — recent posts from tracked Substack publications (read-only; default 1-day window)

### Repository

- `/repo owner/repo`

### Branches

- `/branches owner/repo`

### Help

- `/help`

## Non-Functional Requirements

- Poll GitHub efficiently
- Respect API rate limits
- Cache responses
- Async architecture
- Support hundreds of tracked users
- Recover gracefully after downtime

## Future Enhancements

### Weekly Digest

Weekly engineering summaries highlighting trends and major milestones.

### Repository Trends

- commit velocity
- contributor activity
- inactive repositories
- top contributors

### AI Release Notes

Generate release notes from commit history.

### GitHub Issue Summaries

Summarize:

- opened issues
- closed issues
- pull requests
- reviews

## Newsletter Intelligence (Substack) — Implemented

Substack is the first additional content source alongside GitHub, proving the
"extensible architecture for additional content sources" goal. Publications
expose public RSS feeds (`https://<publication>.substack.com/feed`), so no auth
or scraping is needed. See `docs/spec.md` for the full feature spec and
`docs/implementation.md` for the build.

**As shipped (differs from the original stretch-goal sketch):**

- **Management is admin-panel only** — the **Publications** section of `/admin/*`
  (`/admin/api/publications` CRUD). There is **no** `/substack add|remove|list`
  Discord command; `/substack` is a read-only on-demand view with a `window`
  option (`1d`|`7d`|`30d`, default 1 day).
- **Rendering is the native feed excerpt** (title + link + the feed's own
  description), **not** an AI/LLM summary — zero added inference cost.
- **Delivery:** the daily digest posts **one 📰 message per publication** that has
  new posts (fanned out like the per-user GitHub digest, to the same
  `digest_channel_id`); publications with nothing new post nothing. Same daily
  schedule — no new scheduler, queue, secret, or IAM role.

Example (per-publication message):

```
📰 The Pragmatic Engineer — 2 new posts

"How AI coding assistants are evolving"
The shift from code generation toward workflow automation… https://…/p/ai-assistants

"Enterprise adoption patterns"
A look at how large orgs are rolling out… https://…/p/enterprise
```

Future enhancements could include topic tagging, semantic search across archived
articles, generic (non-Substack) RSS sources, and cross-referencing newsletter
topics with tracked GitHub activity to surface emerging engineering trends.

## Success Metrics

- Daily digest generated successfully >99% of days
- Slash command response time under 3 seconds (excluding AI summarization)
- Support 500+ tracked GitHub users
- Less than 1% duplicate commit reporting
- Users can understand the previous day's engineering activity in under 2 minutes
