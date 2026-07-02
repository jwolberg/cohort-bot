---
title: "feat: GitHub Activity Digest Discord Bot (MVP)"
status: active
created: 2026-07-02
type: feat
depth: deep
target_repo: cohort-bot
origins:
  - docs/PRD.md
  - docs/ARCHITECTURE.md
---

# feat: GitHub Activity Digest Discord Bot (MVP)

**Target repo:** `cohort-bot` (greenfield). All paths below are relative to the
`cohort-bot/` project root. Origin docs: `docs/PRD.md`, `docs/ARCHITECTURE.md`.

---

## Summary

Build a Python Discord bot on Google Cloud Run that tracks selected GitHub users
and posts a daily engineering-activity digest to a Discord channel, plus
on-demand slash commands for repository/branch/user inspection and an Alpine.js
admin panel for managing tracked users and config.

The bot uses Discord **HTTP Interactions** (not a gateway connection) so Cloud
Run can scale to zero, **Cloud Scheduler** to trigger the daily digest,
**Cloud Tasks** to fan out per-user work and run deferred slow-command
follow-ups, **Firestore Native** for persistence, and **Anthropic Claude** to
summarize each user's commits.

This plan covers the MVP end to end: scaffolding, data layer, Discord
interaction handling, slash-command registration + handlers, the GitHub client,
the Claude summarizer, the digest pipeline, the admin panel, deployment
scaffolding, and observability.

---

## Problem Frame

Following dozens of GitHub developers requires manual profile-checking. GitHub
exposes the raw data but no concise daily engineering digest. This bot answers
"who shipped what today, and where" inside Discord, with interactive drill-down
(`/repo`, `/branches`, `/user`) and low operational overhead (serverless,
scale-to-zero, mostly-free-tier infra). See `docs/PRD.md` for full product
framing and `docs/ARCHITECTURE.md` for the resolved technical architecture.

---

## Scope Boundaries

### In scope (MVP)
- Tracking commands: `/track add|remove|list`
- Read commands: `/repo`, `/branches`, `/user`, `/digest today|yesterday`, `/help`
- Daily digest pipeline (Scheduler → per-user fan-out → Claude summary → channel post)
- Firestore Native data layer with commit dedup + per-user cursors + recovery
- GitHub REST client with ETag caching and rate-limit backoff
- Claude summarizer (Haiku default)
- Alpine.js admin panel + JSON API + auth (IAP)
- Deployment scaffolding (Dockerfile, Cloud Build, gcloud provisioning, IAM)
- Structured logging + daily-digest SLO heartbeat

### Deferred to Follow-Up Work
- Weekly digest, repository trends, AI release notes, GitHub issue/PR summaries
  (PRD "Future Enhancements")
- GraphQL batching for GitHub (perf optimization once REST rate limits bind)
- GitHub App auth (start with a PAT; revisit if the 500+ user target strains limits)

### Out of scope (this product's identity)
- Substack / newsletter intelligence (PRD "Stretch Goal")
- Gateway-only Discord features (message listening, presence, voice)

---

## Key Technical Decisions

| Decision | Choice | Rationale / origin |
|---|---|---|
| Discord transport | HTTP Interactions (no gateway) | Fits Cloud Run scale-to-zero (ARCHITECTURE §1) |
| Web framework | FastAPI + Uvicorn | Async, fast cold start, pydantic validation |
| Signature verify | PyNaCl (Ed25519) on every `/interactions` | Discord requirement; reject unverified → 401 |
| Slow commands | Deferred response (`type 5`) + Cloud Tasks follow-up | Beats 3s ACK deadline (ARCHITECTURE §5, §6b) |
| Persistence | Firestore **Native mode**, async client | Serverless, irreversible mode choice — create Native (ARCHITECTURE §7) |
| Async / fan-out | Cloud Tasks (2 queues: digest fan-out, followups) | Retries + isolation (ARCHITECTURE §3) |
| Scheduling | Cloud Scheduler → OIDC HTTP call | Daily trigger |
| GitHub auth | PAT (5,000 req/hr) + ETag conditional requests | Simplest MVP; App deferred |
| Summarizer | Anthropic `claude-haiku-4-5` default | Cost-efficient; Sonnet upgrade path per volume |
| Admin auth | Identity-Aware Proxy (IAP) on `/admin/*` | No custom auth code (ARCHITECTURE §9) |
| Dedup | `processed_commits` doc id = `owner/repo@sha` | Idempotent digest, <1% dup target |
| Recovery | Advance per-user cursor only after successful post | Survives downtime (ARCHITECTURE §6c, §11) |

**Verify at implementation time** (current API specifics): Discord interaction
response type codes and follow-up webhook contract; Cloud Tasks OIDC token setup
for Cloud Run invocation; Firestore async client TTL-policy configuration.

---

## Output Structure

```
cohort-bot/
├── app/
│   ├── main.py                  # FastAPI app + route wiring
│   ├── config.py                # settings; env + Secret Manager
│   ├── logging.py               # structured JSON logging setup
│   ├── discord/
│   │   ├── verify.py            # Ed25519 signature verification
│   │   ├── interactions.py      # /interactions endpoint + dispatch
│   │   ├── commands.py          # command schema definitions
│   │   ├── handlers.py          # per-command handler functions
│   │   ├── responses.py         # embed + response builders
│   │   └── rest.py              # Discord REST (post message, PATCH follow-up)
│   ├── github/
│   │   └── client.py           # httpx client: users, commits, repo, branches
│   ├── store/
│   │   ├── firestore.py        # async Firestore client init
│   │   └── repositories.py     # data access for the 4 collections
│   ├── summarizer/
│   │   └── claude.py           # Anthropic summarizer
│   ├── digest/
│   │   ├── pipeline.py         # fan-out orchestration + assembly
│   │   └── formatter.py        # digest embed formatting
│   ├── tasks/
│   │   ├── queue.py            # Cloud Tasks enqueue helpers
│   │   └── auth.py             # OIDC verification for task/scheduler endpoints
│   └── admin/
│       ├── api.py              # admin JSON API routes
│       └── static/index.html   # Alpine.js + Tailwind (CDN) panel
├── scripts/
│   └── register_commands.py    # register slash commands (guild/global)
├── deploy/
│   ├── Dockerfile
│   ├── cloudbuild.yaml
│   └── setup.sh                # gcloud provisioning (idempotent)
├── tests/
│   ├── conftest.py             # fixtures: Firestore emulator, fake httpx
│   ├── test_verify.py
│   ├── test_repositories.py
│   ├── test_github_client.py
│   ├── test_summarizer.py
│   ├── test_handlers.py
│   ├── test_digest_pipeline.py
│   └── test_admin_api.py
├── pyproject.toml
├── .env.example
└── README.md
```

The tree is a scope declaration; per-unit `**Files:**` are authoritative.

---

## Implementation Units

Grouped into phases by dependency. U-IDs are stable.

### Phase A — Foundation

### U1. Project scaffolding & configuration
- **Goal:** Runnable FastAPI skeleton, dependency + config management, git init.
- **Requirements:** Enables all PRD features; NFR "async architecture".
- **Dependencies:** none.
- **Files:** `pyproject.toml`, `app/main.py`, `app/config.py`, `app/logging.py`,
  `.env.example`, `README.md`, `tests/conftest.py`
- **Approach:** FastAPI app with a `/healthz` route and Uvicorn entrypoint.
  `config.py` loads settings from env vars (local `.env`) and, in deployed envs,
  Secret Manager: `DISCORD_PUBLIC_KEY`, `DISCORD_TOKEN`, `DISCORD_APP_ID`,
  `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `GCP_PROJECT`, queue names, digest
  channel/config. `git init` the project.
- **Patterns to follow:** existing `.gitignore` Python conventions already present.
- **Test scenarios:**
  - `/healthz` returns 200 with expected body.
  - Config loads required settings from env; missing required var raises a clear error at startup.
  - Test expectation: config validation covered; app-wiring smoke test.
- **Verification:** `uvicorn app.main:app` boots; `/healthz` responds; tests pass.

### U2. Firestore Native data layer
- **Goal:** Async data access for the four collections with dedup + cursors.
- **Requirements:** "Activity Cache" (PRD); dedup <1% (Success Metrics).
- **Dependencies:** U1.
- **Files:** `app/store/firestore.py`, `app/store/repositories.py`,
  `tests/test_repositories.py`
- **Approach:** Async Firestore client init (Native mode). Repository functions
  for: `tracked_users` (add/remove/list-enabled/get, per-user `last_cursor`),
  `processed_commits` (doc id `owner/repo@sha`, `has_sha`/`record_shas`,
  ~90d TTL), `repo_cache` (get/put with `etag`, `fetched_at`), `config`
  (singleton: digest channel id, hour, admin role ids). Collection schema per
  ARCHITECTURE §7.
- **Patterns to follow:** ARCHITECTURE §7 collection definitions.
- **Test scenarios:**
  - Add tracked user → appears in list-enabled; duplicate add is idempotent.
  - Remove tracked user → excluded from list-enabled.
  - `record_shas` then `has_sha` returns true; unknown SHA returns false (dedup key correctness).
  - Cursor round-trips: set `last_cursor`, read it back per user.
  - `repo_cache` put/get preserves `etag` and `fetched_at`.
  - Edge: list-enabled with zero users returns empty, not error.
  - Integration: run against the **Firestore emulator** (conftest fixture), not mocks.
- **Verification:** Repository tests pass against the emulator.
- **Execution note:** Stand up the Firestore emulator fixture first; write these as integration tests.

### U3. Discord interaction endpoint + signature verification
- **Goal:** Secure `/interactions` endpoint that verifies signatures and dispatches.
- **Requirements:** Foundation for all slash commands; security (ARCHITECTURE §10).
- **Dependencies:** U1.
- **Files:** `app/discord/verify.py`, `app/discord/interactions.py`,
  `app/discord/responses.py`, `tests/test_verify.py`
- **Approach:** `verify.py` validates the Ed25519 signature using
  `DISCORD_PUBLIC_KEY` over `timestamp + body`. `interactions.py` rejects
  invalid signatures with 401, answers `PING` (type 1) with `PONG`, and routes
  `APPLICATION_COMMAND` (type 2) to a dispatcher (handlers land in U7).
  `responses.py` provides builders for immediate (`type 4`), deferred (`type 5`),
  and ephemeral responses + embeds.
- **Patterns to follow:** ARCHITECTURE §5 (registration vs delivery), §6a/§6b.
- **Test scenarios:**
  - Valid signature + PING → 200 with PONG payload.
  - Invalid/absent signature → 401, body never parsed.
  - Tampered body with otherwise-valid header → 401.
  - Command interaction with valid signature routes to dispatcher (stub).
  - Response builder emits correct type codes for immediate/deferred/ephemeral.
- **Verification:** Signature tests pass with known-good and known-bad vectors.
- **Execution note:** Start with a failing test for the 401-on-bad-signature contract.

### U4. Slash command registration
- **Goal:** Declare command schemas and a script to register them with Discord.
- **Requirements:** All slash commands (PRD "Slash Commands").
- **Dependencies:** U3.
- **Files:** `app/discord/commands.py`, `scripts/register_commands.py`,
  `tests/test_commands.py`
- **Approach:** `commands.py` defines command/subcommand/option schemas for
  `/track (add|remove|list)`, `/repo`, `/branches`, `/user`, `/digest (today|yesterday)`,
  `/help`. `register_commands.py` PUTs them to the guild endpoint (fast, for dev)
  or global endpoint (prod), reading app id + token from config.
- **Patterns to follow:** ARCHITECTURE §5A registration.
- **Test scenarios:**
  - Command schema list validates against expected names/subcommands/option types.
  - `/track` includes exactly the three subcommands with required string options.
  - `/repo` / `/branches` declare `owner/repo` option; `/digest` declares the day subcommand.
  - Test expectation: registration HTTP call mocked (assert correct endpoint + payload); no live Discord call in tests.
- **Verification:** Running the script against a test guild makes commands appear; schema tests pass.

### Phase B — External clients

### U5. GitHub REST client
- **Goal:** Async GitHub client with rate-limit handling and ETag caching.
- **Requirements:** `/repo`, `/branches`, `/user`, digest fetch; NFR rate limits + caching.
- **Dependencies:** U2 (for `repo_cache` ETags).
- **Files:** `app/github/client.py`, `tests/test_github_client.py`
- **Approach:** `httpx.AsyncClient` with PAT auth. Methods: fetch a user's
  recent events/commits since a cursor timestamp; fetch repo metadata
  (description, language, stars, forks, default branch, latest activity, recent
  commits, contributors); fetch branches (name, latest commit, updated time,
  author, ahead/behind when available). Store/send ETags via `repo_cache`; treat
  `304` as cache hit. Bounded concurrency via semaphore; honor
  `X-RateLimit-Remaining` and `Retry-After` with jittered backoff on 403/429.
- **Patterns to follow:** ARCHITECTURE §8.
- **Test scenarios:**
  - Repo fetch maps JSON → expected fields; missing optional fields degrade gracefully.
  - Branch fetch returns list with commit/author/updated fields.
  - Commits-since filters by cursor timestamp boundary (inclusive/exclusive correctness).
  - `304 Not Modified` uses cached `repo_cache` entry and issues no re-parse.
  - Error path: `403` with rate-limit exhausted triggers backoff and surfaces a typed error (not a crash).
  - Error path: `404` for unknown `owner/repo` returns a not-found result the handler can render.
  - Edge: user with zero recent commits returns empty activity, not error.
  - Test expectation: httpx transport mocked with canned responses + headers.
- **Verification:** Client tests pass; rate-limit headers observed in logs when exercised.

### U6. Claude summarizer
- **Goal:** Turn commit messages + repo context into a concise paragraph.
- **Requirements:** "AI-generated summary of work performed" (PRD digest).
- **Dependencies:** U1.
- **Files:** `app/summarizer/claude.py`, `tests/test_summarizer.py`
- **Approach:** Wrap the `anthropic` SDK. Input: commit messages, repo
  description, commit count. Output: one short paragraph. Default
  `claude-haiku-4-5`; allow model override by volume (config). Bound prompt size
  (truncate/aggregate large commit lists). Handle API errors with a graceful
  fallback string so a summary failure never fails the whole digest.
- **Patterns to follow:** ARCHITECTURE §2 (summarizer), Key Decisions.
- **Test scenarios:**
  - Given sample commits + description, builds a prompt containing the messages and count.
  - Returns the model's text on success (SDK mocked).
  - Error path: API failure returns a safe fallback (e.g., "N commits across M repos") without raising.
  - Edge: empty commit list is not summarized (skipped upstream) — assert guard.
  - Edge: very large commit list is truncated/aggregated before the call.
  - Test expectation: Anthropic SDK mocked; assert model id + prompt shape, not live tokens.
- **Verification:** Summarizer tests pass; a manual sample produces a sensible paragraph.

### Phase C — Command handlers & async plumbing

### U7. Command handlers (fast + slow paths)
- **Goal:** Implement all slash-command behaviors.
- **Requirements:** `/track`, `/repo`, `/branches`, `/user`, `/digest`, `/help` (PRD).
- **Dependencies:** U2, U3, U5, U6, U8 (slow-path enqueue).
- **Files:** `app/discord/handlers.py`, `app/discord/rest.py`,
  `tests/test_handlers.py`
- **Approach:** Fast path (`/track *`, `/help`) reads/writes Firestore and
  returns an immediate embed within 3s. Slow path (`/repo`, `/branches`,
  `/user`, `/digest`) returns a deferred ACK, enqueues a Cloud Task to the
  follow-up worker (U8) carrying the interaction token + parsed args, then the
  worker calls GitHub/Claude and PATCHes the follow-up webhook via `rest.py`.
  `/track add` requires admin (role check against `config.admin_role_ids`).
- **Patterns to follow:** ARCHITECTURE §6a/§6b; responses from U3.
- **Test scenarios:**
  - `/track add <user>` by admin → user persisted, confirmation embed; non-admin → ephemeral denial.
  - `/track remove` / `/track list` reflect store state.
  - `/help` returns the command reference embed immediately.
  - `/repo owner/repo` returns deferred ACK **and** enqueues a follow-up task with correct args.
  - Slow-path worker builds the repo embed from GitHub client output and PATCHes the follow-up.
  - Error path: `/repo` on unknown repo → follow-up renders a friendly not-found message.
  - Edge: malformed `owner/repo` input → validation error embed, no GitHub call.
  - Integration: full `/repo` flow (defer → task → GitHub mock → follow-up PATCH) with Discord REST mocked.
- **Verification:** Handler tests pass; manual `/track` + `/repo` work end to end in a test guild.

### U8. Cloud Tasks integration + OIDC-protected worker endpoints
- **Goal:** Enqueue helpers and secured task endpoints for deferred work.
- **Requirements:** NFR async + graceful recovery; slow-command follow-ups.
- **Dependencies:** U1, U3.
- **Files:** `app/tasks/queue.py`, `app/tasks/auth.py`,
  `app/discord/interactions.py` (add `/tasks/followup` route), `tests/test_tasks.py`
- **Approach:** `queue.py` enqueues tasks to the `interaction-followups` and
  `digest-fanout` queues with an OIDC token from the service account.
  `auth.py` verifies inbound OIDC tokens on all `/tasks/*` and
  `/tasks/digest/*` endpoints and rejects unauthenticated callers.
  `/tasks/followup` runs slow-command work (used by U7).
- **Patterns to follow:** ARCHITECTURE §3, §9 (OIDC on task/scheduler endpoints).
- **Test scenarios:**
  - Enqueue builds a task with correct target URL, method, OIDC audience, and payload.
  - `/tasks/*` with missing/invalid OIDC token → 401/403.
  - `/tasks/followup` with valid token dispatches to the follow-up handler.
  - Error path: enqueue failure surfaces a typed error and is logged.
  - Test expectation: Cloud Tasks client + token verification mocked.
- **Verification:** Task auth tests pass; a real enqueue lands and executes in a deployed env.

### Phase D — Digest pipeline

### U9. Daily digest pipeline
- **Goal:** Scheduled digest: fan out per user, summarize, assemble, post, dedup.
- **Requirements:** "Daily Digest" (PRD); >99% success, <1% dup, downtime recovery.
- **Dependencies:** U2, U5, U6, U8.
- **Files:** `app/digest/pipeline.py`, `app/digest/formatter.py`,
  `app/discord/interactions.py` (add `/tasks/digest/run` + `/tasks/digest/user`),
  `app/discord/rest.py`, `tests/test_digest_pipeline.py`
- **Approach:** `/tasks/digest/run` (Scheduler-triggered, OIDC) loads enabled
  users and enqueues one `/tasks/digest/user` task each. Each user task: fetch
  commits since `last_cursor`, filter out SHAs in `processed_commits`, group by
  repo, summarize via Claude, persist a per-user digest section. A final
  assembly step formats sections into the channel message (per ARCHITECTURE §14
  open decision: one message per user vs batched embed — default batched with
  pagination fallback for Discord size limits) and posts via `rest.py`. On
  successful post, record new SHAs and advance each user's cursor (idempotent →
  re-run safe). `/digest today|yesterday` reuses the assembly on demand.
- **Patterns to follow:** ARCHITECTURE §6c; formatter mirrors PRD digest example.
- **Test scenarios:**
  - Fan-out enqueues exactly one task per enabled user.
  - Per-user task filters already-processed SHAs (no duplicate reporting).
  - Assembly formats multi-user, multi-repo sections matching the PRD layout.
  - Cursor advances **only after** a successful post (simulate post failure → cursor unchanged, SHAs not recorded).
  - Re-running the same day posts nothing new (idempotency).
  - Edge: user with no new commits is omitted or shown as inactive per formatter rule.
  - Edge: digest exceeding Discord embed limits paginates instead of truncating silently.
  - Error path: one user's GitHub/Claude failure is isolated (Cloud Tasks retry) and doesn't sink the whole digest.
  - Covers AE (PRD digest example): 8 users tracked, per-user commit counts + repo summaries render.
  - Integration: run against Firestore emulator with GitHub/Claude/Discord mocked.
- **Verification:** Pipeline tests pass; a manual triggered run posts a correct digest to a test channel.
- **Execution note:** Write the idempotency/cursor test first — it encodes the core recovery guarantee.

### Phase E — Admin, deployment, observability

### U10. Admin panel (JSON API + Alpine.js UI + IAP)
- **Goal:** Web UI to manage tracked users and config, mirroring `/track`.
- **Requirements:** "User Tracking" admin management; ARCHITECTURE §9.
- **Dependencies:** U2, U9 (test-digest trigger).
- **Files:** `app/admin/api.py`, `app/admin/static/index.html`,
  `tests/test_admin_api.py`
- **Approach:** Same-origin JSON API: `GET/POST/DELETE /admin/api/users`,
  `GET/PUT /admin/api/config`, `POST /admin/api/digest/test`. Static
  `index.html` uses Alpine.js + Tailwind (CDN), no build step, served by the
  app. `/admin/*` fronted by IAP; API handlers trust IAP-verified identity
  (fallback: shared admin bearer token from Secret Manager). API and `/track`
  are two front-ends over the same repositories (parity).
- **Patterns to follow:** ARCHITECTURE §9; reuse U2 repositories.
- **Test scenarios:**
  - `GET /admin/api/users` returns tracked users; `POST` adds; `DELETE` removes (same store as `/track`).
  - `PUT /admin/api/config` updates digest channel/hour; `GET` reads back.
  - `POST /admin/api/digest/test` enqueues a test digest run.
  - Auth: request without IAP identity / admin token → 401.
  - Edge: adding a duplicate user is idempotent and reflected in the UI list.
  - Test expectation: API tests against emulator; static HTML smoke-checked for required elements/endpoints.
- **Verification:** API tests pass; panel loads, lists/add/removes users, triggers a test digest.

### U11. Deployment scaffolding (Cloud Run + GCP resources)
- **Goal:** Reproducible build + provisioning of all GCP resources.
- **Requirements:** NFR low ops; ARCHITECTURE §3 resource list.
- **Dependencies:** U1 (app must build); logically after U2–U10 exist to deploy.
- **Files:** `deploy/Dockerfile`, `deploy/cloudbuild.yaml`, `deploy/setup.sh`,
  `README.md` (ops section)
- **Approach:** `Dockerfile` builds the FastAPI app (Uvicorn). `cloudbuild.yaml`
  builds + pushes to Artifact Registry + deploys to Cloud Run. `setup.sh`
  (idempotent gcloud) provisions: Firestore **Native** database, Cloud Scheduler
  `daily-digest` job (OIDC → `/tasks/digest/run`), Cloud Tasks queues
  `digest-fanout` + `interaction-followups`, Secret Manager secrets, service
  account `digest-bot-sa` with least-privilege IAM (`datastore.user`,
  `cloudtasks.enqueuer`, `secretmanager.secretAccessor`, `run.invoker`), and IAP
  on the admin routes. README documents deploy + Discord interaction-endpoint
  URL setup.
- **Patterns to follow:** ARCHITECTURE §3, §9, §12.
- **Test scenarios:**
  - Test expectation: none (infra scaffolding) — validate via a real deploy to a dev GCP project and a smoke test of `/healthz` + `/interactions` PING.
- **Verification:** `setup.sh` provisions cleanly on a fresh project; Cloud Run deploy serves `/healthz`; Discord accepts the interaction endpoint URL (PING → PONG); Scheduler fires a run.
- **Execution note:** Firestore mode is chosen at database creation and is irreversible — `setup.sh` must create **Native mode**.

### U12. Observability & digest SLO
- **Goal:** Structured logs and a daily-digest success signal.
- **Requirements:** Success Metrics (>99% digest success); ARCHITECTURE §11.
- **Dependencies:** U1, U9.
- **Files:** `app/logging.py` (extend), `app/digest/pipeline.py` (emit heartbeat),
  `deploy/setup.sh` (alert policy), `tests/test_logging.py`
- **Approach:** Structured JSON logs per run (users processed, commits found, API
  calls, rate-limit headroom, LLM tokens, duration). Emit a heartbeat
  log/metric on successful digest post; a Cloud Monitoring alert pages if the
  daily digest fails to post. Idempotency/recovery already covered by U9.
- **Patterns to follow:** ARCHITECTURE §11.
- **Test scenarios:**
  - Digest run emits a structured log entry with the expected fields.
  - Successful post emits the heartbeat marker; failed post does not.
  - Test expectation: assert log record contents; alert policy validated in the deploy smoke test.
- **Verification:** Logs show structured run records; a forced failure does not emit the heartbeat (alert would fire).

---

## System-Wide Impact

- **External contracts:** Discord interaction-endpoint URL + registered command
  schemas; GitHub PAT scope; Anthropic API key; GCP project resources. Changing
  command schemas requires re-running `register_commands.py`.
- **Data:** Firestore Native mode is a one-time irreversible project choice.
- **Security surfaces:** `/interactions` (Ed25519), `/tasks/*` and
  `/tasks/digest/*` (OIDC), `/admin/*` (IAP). All must reject unauthenticated
  callers.
- **Cost:** Dominated by Anthropic tokens; the rest sits in free tiers at MVP
  scale (ARCHITECTURE §12).

---

## Risk Analysis & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Cold-start misses the 3s ACK | Command fails | Defer all slow commands; consider `min-instances=1` if observed (ARCHITECTURE §11) |
| GitHub rate limits at 500+ users | Incomplete digest | ETag conditional requests, per-user cursors, bounded concurrency; **log** when rate-limited rather than silently truncating; GraphQL/App auth deferred |
| Duplicate commit reporting | Noisy digest | `processed_commits` dedup key + cursor-after-post advance |
| Partial failure mid-digest | Missing users | Per-user Cloud Tasks isolation + retries; idempotent re-run |
| Firestore mode chosen wrong | Irreversible rework | `setup.sh` creates Native mode explicitly; called out in U11 |
| Summary API failure | Digest blocked | Graceful fallback string in U6 |
| Discord embed size limits | Truncated digest | Pagination fallback in U9 formatter |

---

## Dependencies / Prerequisites

- GCP project with billing; `gcloud` authenticated.
- Discord application (app id, bot token, public key); a test guild for dev.
- GitHub PAT; Anthropic API key.
- Local: Python 3.12, Firestore emulator, Cloud Tasks (emulator or dev queue),
  a tunnel (e.g. ngrok) to point Discord's interaction URL at local FastAPI
  (ARCHITECTURE §13).

---

## Sequencing

```
U1 ─┬─ U2 ─┬────────────── U7 ── U9 ── U10
    ├─ U3 ─┴─ U4           │      │      │
    ├─ U5 ─────────────────┤      │      │
    ├─ U6 ─────────────────┘      │      │
    └─ U8 ────────────────────────┘      │
   U11 (deploy) after A–E exist;  U12 after U9
```

Phase A (U1–U4) unblocks everything. U5/U6 are independent clients. U7 needs
U2/U3/U5/U6/U8. U9 needs the pipeline pieces. U10–U12 close out admin, deploy,
and observability.

---

## Deferred to Implementation

- Exact Discord response type codes / follow-up webhook contract — verify against
  current Discord docs at U3/U7.
- Cloud Tasks OIDC audience wiring specifics — verify at U8/U11.
- Firestore async client TTL-policy API — verify at U2/U11.
- Final digest message shape (one-per-user vs batched) — decide at U9 against
  real Discord embed limits.
- GitHub events-vs-commits endpoint choice for efficient per-user fetch — settle
  at U5 against real rate-limit behavior.
