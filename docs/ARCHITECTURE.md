# Architecture: GitHub Activity Digest Discord Bot

This document defines the technical architecture for the bot described in
[PRD.md](./PRD.md), targeting **Google Cloud Run + Firestore + Python**.

---

## 1. Key architectural decision: HTTP Interactions, not a Gateway bot

The PRD references Hikari (a Discord **gateway** library). A gateway bot holds a
persistent WebSocket to Discord, which requires an **always-on** process — it
conflicts with Cloud Run's scale-to-zero model and raises cost/ops.

**Decision:** Use Discord's **HTTP Interactions** model instead.

- Discord POSTs each slash-command invocation to our Cloud Run URL (a signed,
  webhook-style request).
- No persistent connection → Cloud Run can **scale to zero**.
- Slow commands (GitHub / LLM calls) use Discord's **deferred response**
  (`type 5`) + follow-up webhook, with the slow work offloaded to **Cloud Tasks**.
- The daily digest is push-only (bot → channel via webhook/REST) and is
  triggered by **Cloud Scheduler**, so it needs no inbound connection either.

**Tradeoff:** Loses gateway-only features (presence, real-time events, message
listening). None are required by the PRD. If those are ever needed, the fallback
is a gateway bot on Cloud Run with `min-instances=1` and CPU always allocated —
documented in §11.

---

## 2. Technology stack

| Concern | Choice | Rationale |
|---|---|---|
| Language | **Python 3.12** | PRD requirement; strong GCP + Discord + LLM SDKs |
| Web framework | **FastAPI + Uvicorn** | Async, fast cold start, native pydantic validation for interaction payloads |
| Discord layer | **HTTP Interactions** via `PyNaCl` (Ed25519 signature verify) + raw REST | No gateway; fits Cloud Run. `discord-interactions` optional helper |
| HTTP client | **httpx** (async) | Concurrent GitHub calls, connection pooling, ETag support |
| GitHub access | **GitHub REST API** via httpx | Fine-grained control over rate limits + conditional requests. (GraphQL is a future optimization for fan-in queries) |
| LLM summarizer | **Anthropic Claude** (`anthropic` SDK) — `claude-haiku-4-5` default, `claude-sonnet-5` for richer digests | Cost-efficient summarization; upgrade per-call by content volume |
| Persistence | **Firestore (Native mode)** via `google-cloud-firestore` async client | Serverless, scales to zero, generous free tier, no connection pooling. **Locked in** (§7) |
| Async / fan-out | **Cloud Tasks** | Deferred command follow-ups + per-user digest fan-out with retries |
| Scheduling | **Cloud Scheduler** | Cron trigger for the daily digest (OIDC-authenticated HTTP) |
| Secrets | **Secret Manager** | Discord token & public key, GitHub token, Anthropic key |
| Admin frontend | **Alpine.js + Tailwind (CDN)**, single static page | Optimal for a small CRUD panel — zero build step, served by the same service (§9) |
| Container | **Docker** on Cloud Run | Single deployable image (API + static admin) |
| IaC / deploy | **gcloud** + Cloud Build (optionally Terraform) | Matches requested tooling; reproducible deploys |

---

## 3. GCP resources

| Resource | Purpose |
|---|---|
| **Cloud Run** service `digest-bot` | Serves Discord interactions, digest task handler, and admin UI |
| **Firestore** (Native mode) | `tracked_users`, `processed_commits`, `repo_cache`, `config` |
| **Cloud Scheduler** job `daily-digest` | Fires once/day → `POST /tasks/digest/run` (OIDC) |
| **Cloud Tasks** queue `digest-fanout` | One task per tracked user; retries on failure |
| **Cloud Tasks** queue `interaction-followups` | Deferred slash-command work |
| **Secret Manager** | `DISCORD_TOKEN`, `DISCORD_PUBLIC_KEY`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY` |
| **Artifact Registry** | Container images |
| **Cloud Build** | CI: build + deploy on push |
| **Service Account** `digest-bot-sa` | Least-privilege: Firestore, Tasks enqueue, Secret access, Scheduler invoker |
| **Cloud Logging / Monitoring** | Structured logs, digest-success SLO alert (§10) |

---

## 4. Deployment topology

```
                       ┌──────────────────────────────┐
   Discord  ──POST──▶  │  Cloud Run: digest-bot        │
   (slash cmd,         │  FastAPI (Uvicorn)            │
    signed)            │  ├─ /interactions  (verify +  │
                       │  │   ACK/defer)               │
   Cloud Scheduler ──▶ │  ├─ /tasks/digest/run (fanout)│──▶ Cloud Tasks
   (daily cron)        │  ├─ /tasks/digest/user (work) │◀── (per-user)
                       │  ├─ /tasks/followup   (work)  │◀── Cloud Tasks
   Admin browser ────▶ │  └─ /admin/* + static SPA     │
                       └───────┬───────────┬──────────┘
                               │           │
                     ┌─────────▼──┐   ┌────▼─────────┐
                     │ Firestore  │   │ Secret Mgr   │
                     └────────────┘   └──────────────┘
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                   ▼
        GitHub REST API   Anthropic API      Discord REST
                                             (post digest /
                                              follow-ups)
```

---

## 5. Slash commands over HTTP Interactions

Slash commands are fully supported on Cloud Run — HTTP Interactions is Discord's
**native** transport for them, not a workaround. There are two independent
concerns:

**A. Registration (one-time, out of band).** We declare each command's schema
(name, description, options, subcommands) with a REST `PUT` to
`applications/{app_id}/commands` (global) or `.../guilds/{guild_id}/commands`
(instant, per-guild — used in dev). This is done by a deploy-time script, not on
the request path. Supports subcommands (`/track add|remove|list`), typed
options, and autocomplete.

**B. Delivery (per invocation).** Discord POSTs the invocation to our
`/interactions` endpoint. We verify the Ed25519 signature, then respond within
Discord's **3-second deadline**:

- **Fast commands** → respond immediately with the result (`type 4`).
- **Slow commands** (GitHub / LLM) → respond immediately with a **deferred ACK**
  (`type 5`, "Bot is thinking…"), offload the real work to Cloud Tasks, then
  **PATCH the follow-up webhook** with the final embed. The interaction token
  stays valid ~15 minutes.

**Interaction endpoint setup:** set the app's *Interactions Endpoint URL* in the
Discord Developer Portal to the Cloud Run `/interactions` URL. Discord sends a
`PING` on save and expects a signed `PONG`, so the endpoint must be deployed
before it can be registered.

This model gives full slash-command capability (subcommands, options,
autocomplete, ephemeral replies). The only unsupported features are
gateway-only ones (arbitrary message reads, presence, voice) — none required by
the PRD.

---

## 6. Request & job flows

### 6a. Slash command (fast path — `/track list`, `/help`)
1. Discord POSTs signed interaction to `/interactions`.
2. Verify Ed25519 signature (reject 401 on failure — required by Discord).
3. Handle `PING` → `PONG`; otherwise read from Firestore and return an
   immediate embed (well under the 3s ACK deadline).

### 6b. Slash command (slow path — `/repo`, `/branches`, `/user`, `/digest`, `/substack`)
1. Verify + immediately return **deferred** response (`type 5`) to beat the 3s
   deadline.
2. Enqueue a **Cloud Task** to `/tasks/followup` with the interaction token.
3. Task worker calls GitHub (+ LLM if needed) — or, for `/substack`, fetches the
   tracked feeds (no LLM) — then PATCHes the follow-up webhook with the final
   embed. Interaction tokens are valid ~15 min.

### 6c. Daily digest
1. Cloud Scheduler → `POST /tasks/digest/run`.
2. Handler loads enabled `tracked_users` **and** enabled `tracked_publications`,
   enqueuing one Cloud Task per user to `/tasks/digest/user` **and** one per
   publication to `/tasks/substack/publication` (fan-out for parallelism +
   isolated retries; both reuse the `digest-fanout` queue).
3. Each user task: fetch events/commits since last cursor → filter out SHAs in
   `processed_commits` → summarize via Claude → write results.
   Each publication task: fetch the RSS feed since its cursor → filter out post
   ids in `processed_posts` → render the **native excerpt** (no LLM).
4. Each task posts its section (per-user embed / per-publication 📰 message) to
   `digest_channel_id` via Discord REST.
5. Record new SHAs / post ids and advance the per-user / per-publication cursor
   **only after** a successful post (idempotent → recovers after downtime).

---

## 7. Data layer

**Firestore Native mode** (locked in) via the `google-cloud-firestore` async
client. It is the modern, serverless Firestore mode — scales to zero, needs no
connection management on Cloud Run, and supports TTL policies + collection-group
queries. (Note: a project is Native mode *or* Datastore mode, never both, and
the choice is made once at project creation — create the project in **Native
mode**.)

### Collections (Firestore Native)

```
tracked_users/{username}
  username: string
  enabled: bool
  added_by: string          # Discord user id
  created_at: timestamp
  last_cursor: timestamp     # last processed event time (per-user watermark)

processed_commits/{repo__sha}   # doc id = "owner/repo@sha" (dedup key)
  repo: string
  sha: string
  processed_at: timestamp
  # TTL policy: auto-expire after ~90d to bound growth

repo_cache/{owner__repo}
  repo: string
  description, language: string
  stars, forks: int
  default_branch: string
  updated_at: timestamp
  fetched_at: timestamp       # cache freshness for TTL checks
  etag: string                # for GitHub conditional requests

config/{singleton}
  digest_channel_id: string
  digest_hour_utc: int
  admin_role_ids: [string]

tracked_publications/{slug}     # doc id = feed host, e.g. "x.substack.com"
  slug: string
  feed_url: string
  title: string
  enabled: bool
  added_by: string
  created_at: timestamp
  last_cursor: timestamp        # init = add time (never dumps the back catalog)

processed_posts/{slug@post_id}  # doc id = "slug@<encoded guid/link>" (dedup key)
  slug: string
  post_id: string
  processed_at: timestamp
  # TTL policy on expire_at: auto-expire after ~90d to bound growth
```

Indexes: single-field indexes cover the MVP queries (`enabled == true`,
ordering by `created_at`). Add composite indexes only if later filtering
demands them.

---

## 8. GitHub client — rate limits & caching

- **Auth:** a GitHub PAT (or GitHub App for higher limits) → 5,000 req/hr.
- **Conditional requests:** store ETags in `repo_cache`; `304 Not Modified`
  responses don't count against the rate limit.
- **Efficient fetch:** prefer the per-user Events API / commits-since-timestamp
  over enumerating every repo. Respect `X-RateLimit-Remaining`; back off with
  jitter on `403`/`429` and honor `Retry-After`.
- **Concurrency:** bounded `httpx.AsyncClient` (semaphore) so a large tracked
  list can't exhaust the budget in one burst.
- **Scale note:** 500+ users may exceed one hourly window — mitigate with
  conditional requests, per-user cursors, and (future) GraphQL batching. Log
  when a run is rate-limited rather than silently truncating.

---

## 9. Admin frontend

**Recommendation: a single static page using Alpine.js + Tailwind (CDN),**
served by the same Cloud Run service at `/admin`. For a small CRUD panel
(add/remove/list tracked users, set digest channel/time, trigger a test digest)
this is optimal — no build step, no separate deploy, instant load.

- The page talks to a small JSON API (`/admin/api/users`, `/admin/api/config`,
  `/admin/api/digest/test`) on the same origin.
- **Auth:** front the `/admin/*` routes with **Identity-Aware Proxy (IAP)** so
  only allow-listed Google accounts reach them — no custom auth code. Simpler
  fallback: a shared admin bearer token in Secret Manager.
- Discord `/track` commands and this panel are two front-ends over the **same**
  API/data, keeping parity.

> **Alternative:** If you want to match your existing **React + Vite + Tailwind**
> convention, build the panel as a small Vite SPA and serve its static bundle
> from the same service. That adds a build step for richer UI later; Alpine.js is
> the lower-overhead default for MVP.

---

## 10. Security & secrets

- All tokens in **Secret Manager**, injected as env vars or read at cold start;
  never in the image or repo.
- **Ed25519 signature verification** on every `/interactions` request (Discord
  requirement; unverified requests → 401).
- Task/scheduler endpoints require **OIDC** tokens from the service account;
  reject unauthenticated callers.
- `digest-bot-sa` gets least-privilege IAM: `datastore.user`,
  `cloudtasks.enqueuer`, `secretmanager.secretAccessor`, `run.invoker`.
- Admin routes gated by IAP (or admin token). No PII beyond public GitHub +
  Discord IDs is stored.

---

## 11. Observability & reliability

- **Structured JSON logs** (Cloud Logging) per run: users processed, commits
  found, API calls, rate-limit headroom, LLM tokens, duration.
- **SLO alert:** page if the daily digest fails to post (ties to PRD's ">99% of
  days"). Emit a heartbeat metric on successful post.
- **Idempotency & recovery:** cursors + `processed_commits` dedup make re-runs
  safe; Cloud Tasks retries per-user failures without re-posting duplicates
  (PRD's "<1% duplicate reporting", "recover gracefully after downtime").
- **Cold-start guard:** if 3s ACK misses become an issue, defer *all* commands
  and/or set `min-instances=1`.

---

## 12. Cost profile (rough, low volume)

- **Cloud Run:** scale-to-zero → near-free at MVP traffic (free tier covers it).
- **Firestore:** free tier (50k reads / 20k writes per day) covers a daily
  digest over hundreds of users.
- **Cloud Scheduler:** 3 free jobs.
- **Cloud Tasks:** first 1M operations/month free.
- **Anthropic API:** the only usage-based cost — Haiku for summaries keeps a
  daily run at cents/day for hundreds of users; Sonnet costs more per digest.

Dominant cost is LLM tokens; everything else sits in free tiers at MVP scale.

---

## 13. Local development

- **Firestore emulator** + **Cloud Tasks** run locally (or a dev queue).
- Expose the local FastAPI via a tunnel (e.g. `ngrok`) and point a **dev Discord
  app's** Interactions Endpoint URL at it for real command testing.
- Register slash commands against a single **test guild** for instant updates
  (global command propagation is slow).
- `.env` for local secrets; Secret Manager in deployed envs.

---

## 14. Open decisions / follow-ups

1. **GitHub PAT vs GitHub App** — App gives higher/again-scaling rate limits for
   the 500+ user target; PAT is simpler for MVP.
2. **LLM default model** — Haiku (cost) vs Sonnet (quality); could auto-escalate
   by commit volume.
3. **Admin auth** — IAP (recommended) vs shared admin token.
4. **Digest assembly** — post one message per user vs a single batched embed
   (Discord embed size/character limits may force pagination for large orgs).

_Resolved: **Firestore Native mode** (§7), **HTTP Interactions** transport (§1, §5)._
