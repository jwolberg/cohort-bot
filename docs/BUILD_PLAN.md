# Build Plan

## Project
- **Name:** Substack Publication Tracking (second content source for the digest bot)
- **Summary:** Add Substack as a tracked source alongside GitHub. Publications are
  managed in the admin panel; new posts are folded into the daily digest as a
  Substack section and are also viewable on demand via `/substack`. Rendering is
  native feed excerpt only — **no LLM summarization**. Reuses the existing
  source/store/digest/admin patterns; no new secret, IAM role, queue, or scheduler.

## Source of Truth (authority order)
1. **`/docs/spec.md`** — authoritative feature spec: scope, decisions, acceptance
   criteria, edge cases, unit breakdown (used as primary; no `challenge.md` exists).
2. **`/docs/ARCHITECTURE.md`** — structure / interfaces (§5 commands, §6c digest
   flow, §7 collections, §8 client, §9 admin, §10 security, §11 observability).
3. **`/docs/PRD.md`** — base-product scope; names *"extensible architecture for
   additional content sources"* as a goal (the umbrella this feature fills).
- **Not used:** `/docs/ux.md` (absent); `/docs/challenge.md` (absent).
- **Note:** the base GitHub bot's own build plan lives at
  `docs/plans/2026-07-02-001-feat-github-digest-discord-bot-plan.md`; this plan is
  scoped to the Substack feature only and does not restate it.

## Planning Assumptions
Carried from `spec.md` "Open Questions" — minimal, flagged, revisit at P1/P2:
- **A1. RESOLVED (2026-07-02):** parse with **stdlib + `defusedxml`** —
  `defusedxml.ElementTree` (XML-bomb hardened), `email.utils.parsedate_to_datetime`
  (RFC-822 dates), `html.parser` (excerpt cleaning). `defusedxml` is the only
  net-new dependency. (`feedparser` and pure-stdlib were considered and rejected —
  see spec Constraints.)
- **A2.** `/substack` uses a fixed **7-day** window (no selectable option in v1).
- **A3.** The scheduled Substack section is a **single combined message** posted to
  `digest_channel_id` after the GitHub digest header (not per-publication messages).
- **A4.** Publications post to the **existing** `digest_channel_id`; no new config key.
- **A5.** A newly added publication's cursor is initialized to **add-time** so the
  back catalog is never dumped (spec Edge Cases; ARCH §7 watermark semantics).

## Architecture Notes
- **Stack (ARCH §2):** FastAPI on Cloud Run, Firestore Native async client, Cloud
  Tasks (OIDC), Cloud Scheduler, Discord HTTP Interactions, `httpx` async.
- **Module layout to mirror:** `app/github/client.py` → `app/substack/client.py`;
  `app/store/repositories.py` repos + `Repositories` bundle; `app/digest/pipeline.py`
  + `formatter.py`; `app/tasks/queue.py`; `app/discord/{commands,handlers,interactions}.py`;
  `app/admin/api.py` + `static/index.html`.
- **Cross-cutting constraints:**
  - **Idempotency / data-flow ordering (ARCH §6c):** scheduled path must **post
    first, then record dedup keys + advance cursor** — a Cloud Tasks retry after a
    failed post must recompute and repost.
  - **Trust boundary (ARCH §10):** `/admin/*` via `require_admin` (verified IAP or
    bearer; unsigned IAP email header never trusted alone); `/tasks/*` via
    `require_oidc` (audience + SA-email match).
  - **Firestore ids (ARCH §7):** no `/`; reuse `_encode()`; TTL via `expire_at`.
  - **Best-effort per feed (ARCH §8 parity):** one bad feed is skipped + logged,
    never fails the run or command.
- **Non-goals affecting implementation (spec Scope/Out of scope):** no Claude
  summarizer changes; no per-channel routing / separate Substack channel; no Discord
  management command; no historical backfill; no generic (non-Substack) RSS.

## Current Status
- **Overall status:** Not Started
- **Current phase:** Phase 1 — Data Layer
- **Current ticket:** P1-T1
- **Blockers:** None (A1 resolved: stdlib + `defusedxml`)

---

## Phase Breakdown

### Phase 1 — Data Layer
**Goal**
- Persist tracked publications and processed-post dedup keys in Firestore, threaded
  through the shared `Repositories` bundle.

**Exit Criteria**
- `TrackedPublicationsRepo` and `ProcessedPostsRepo` exist, are in the `Repositories`
  dataclass + `get_repositories()`, and pass emulator tests.

**Tickets**
- **P1-T1 — Publications + processed-posts repositories**
  - Objective: Add `TrackedPublicationsRepo` (collection `tracked_publications`, doc
    id = slug) with `add/remove/get/list_enabled/list_all/get_cursor/set_cursor`
    (idempotent re-add preserving `created_at`/`last_cursor`, cursor init = add-time
    per A5); add `ProcessedPostsRepo` (collection `processed_posts`, doc id
    `slug@post_id`, `expire_at` 90d TTL) with `has_post`/`record_posts` (batched,
    empty-input no-op). Add both to the `Repositories` dataclass and
    `get_repositories()`.
  - Modules / files: `app/store/repositories.py`.
  - Depends on: none (existing store module).
  - Acceptance criteria: spec AC#1, AC#2 (doc shape), AC#5/#6 (dedup semantics),
    AC#9 (`expire_at`); mirrors ARCH §7 collection conventions (`_encode`, watermark,
    TTL). Doc shape: `{slug, feed_url, title, enabled, added_by, created_at,
    last_cursor}`.
  - Commit: one commit — "feat(store): tracked_publications + processed_posts repos (S1)".
  - Status: Todo

### Phase 2 — Substack Source Client
**Goal**
- Fetch and parse public Substack RSS/Atom feeds into typed post objects,
  best-effort and test-injectable.

**Exit Criteria**
- `SubstackClient.fetch_posts_since` returns correct, newest-first `PostRef`s for RSS
  and Atom fixtures, filters by `since`, cleans excerpts, and skips malformed feeds.

**Tickets**
- **P2-T1 — Substack async client + parser**
  - Objective: `app/substack/client.py`: `SubstackClient` (async context manager,
    injectable `httpx.AsyncClient`, typed `SubstackError`/`NotFoundError`, bounded
    concurrency), `PostRef` dataclass `{slug, post_id, title, url, author, published:
    datetime(UTC), excerpt}`, `fetch_posts_since(feed_url, since, *, limit)`, an
    HTML→text excerpt cleaner (strip tags, unescape entities, truncate ~280), and a
    `feed_url`/URL → slug (host) normalizer. Parse RSS 2.0 via
    `defusedxml.ElementTree` (A1); dates via `email.utils.parsedate_to_datetime`;
    excerpts via `html.parser`; cap the response byte size before parsing. Dedup
    key = entry `guid`, fallback `link`; skip entries missing both.
  - Modules / files: `app/substack/__init__.py`, `app/substack/client.py`; add
    `defusedxml` to `pyproject.toml` runtime deps (+ `.env.example` only if a setting
    is added — none expected).
  - Depends on: none (parallelizable with P1-T1). A1 resolved (stdlib + `defusedxml`).
  - Acceptance criteria: spec AC#4, AC#8; mirrors `GitHubClient` shape (ARCH §8):
    async CM, injectable client, typed errors, best-effort per-feed skip.
  - Commit: "feat(substack): async RSS/Atom client + PostRef parser (S2)".
  - Status: Todo

### Phase 3 — Digest Integration
**Goal**
- Compute a deduped Substack section and post it on the daily schedule with
  retry-safe cursor advance; expose a no-dedup on-demand computation for `/substack`.

**Exit Criteria**
- Scheduled run enqueues one substack-check task; the worker posts only new posts and
  advances cursors only after a successful post; on-demand computation skips dedup.

**Tickets**
- **P3-T1 — Substack section compute + formatter + pipeline factory**
  - Objective: Add `PublicationSection` dataclass and a
    `compute_substack_section(client, publications, *, dedup)` method to
    `DigestPipeline`; extend `DigestPipeline.__init__` with an injectable
    `substack_factory` (mirroring `gh_factory`). Add `format_substack_section(...)`
    to `app/digest/formatter.py` (title + link + cleaned excerpt; respect embed
    limits via existing pagination). Combined single message per A3.
  - Modules / files: `app/digest/pipeline.py`, `app/digest/formatter.py`.
  - Depends on: P1-T1, P2-T1.
  - Acceptance criteria: spec AC#5 (dedup filter), AC#7 (on-demand no dedup);
    ARCH §6c section-compute pattern; rendering = native excerpt (spec Decision 2).
  - Commit: "feat(digest): substack section compute + formatter (S3a)".
  - Status: Todo
- **P3-T2 — Substack scheduled worker + enqueue + wiring**
  - Objective: Add `SUBSTACK_CHECK_PATH = "/tasks/substack/check"` and
    `enqueue_substack_check()` to `app/tasks/queue.py` (reuse the `digest-fanout`
    queue). Add `process_substack()` to the pipeline (post-first-then-record-then-
    advance-cursor). `run_fanout()` also enqueues exactly one substack-check task.
    Add the `/tasks/substack/check` route in `app/discord/interactions.py` guarded by
    `require_oidc`, and wire the handler in `install_digest()`.
  - Modules / files: `app/tasks/queue.py`, `app/digest/pipeline.py`,
    `app/discord/interactions.py`.
  - Depends on: P3-T1.
  - Acceptance criteria: spec AC#5 (one task enqueued, post-first ordering), AC#6
    (idempotent no-op on no new posts), AC#10 (no new queue/scheduler); ARCH §6c
    (fan-out idempotency), §10 (`require_oidc`).
  - Commit: "feat(digest): substack check worker + enqueue (S3b/S4)".
  - Status: Todo

### Phase 4 — User & Admin Surfaces
**Goal**
- Let consumers pull recent posts on demand and let admins manage publications.

**Exit Criteria**
- `/substack` returns recent posts (no dedup); admin panel adds/lists/removes
  publications with auth enforced.

**Tickets**
- **P4-T1 — `/substack` on-demand command**
  - Objective: Add `SUBSTACK_COMMAND` to `app/discord/commands.py` (optional `window`
    string option, default 7d per A2), append to `COMMANDS`. Add `"substack"` to
    `SLOW_COMMANDS`, a `run_followup` branch calling the on-demand (`dedup=False`)
    compute → `edit_original_response`, and update `HELP_TEXT`. Confirm
    `scripts/register_commands.py` picks up `COMMANDS`.
  - Modules / files: `app/discord/commands.py`, `app/discord/handlers.py`,
    `scripts/register_commands.py` (verify only).
  - Depends on: P3-T1 (on-demand compute), P1-T1 (enabled publications).
  - Acceptance criteria: spec AC#7; ARCH §5 (command schema), §6b (slow-path defer
    → follow-up → PATCH original response).
  - Commit: "feat(discord): /substack on-demand command (S5)".
  - Status: Todo
- **P4-T2 — Admin publications CRUD + panel**
  - Objective: Add `/admin/api/publications` GET/POST/DELETE to `app/admin/api.py`
    (`Depends(require_admin)`, `Depends(get_repos)`); POST `{feed_url}` normalizes +
    stores (init cursor = add-time), DELETE by slug. Add a Publications section to
    `app/admin/static/index.html` (Alpine.js add/list/remove rows).
  - Modules / files: `app/admin/api.py`, `app/admin/static/index.html`.
  - Depends on: P1-T1.
  - Acceptance criteria: spec AC#1, AC#2, AC#3; ARCH §9 (admin CRUD parity), §10
    (unsigned IAP email header rejected — regression parity with users).
  - Commit: "feat(admin): publications CRUD + panel section (S6)".
  - Status: Todo

### Phase 5 — Deploy & Docs
**Goal**
- Provision the new collection's TTL and record the feature in the design docs.

**Exit Criteria**
- `deploy/setup.sh` enables the `processed_posts` TTL; PRD/ARCHITECTURE/impl-notes
  updated.

**Tickets**
- **P5-T1 — TTL policy + documentation**
  - Objective: Add `gcloud firestore fields ttls update expire_at
    --collection-group=processed_posts --enable-ttl` (idempotent) to
    `deploy/setup.sh`. Update `docs/PRD.md` (add Substack under sources/commands),
    `docs/ARCHITECTURE.md` (§7 collections: `tracked_publications`, `processed_posts`;
    §5 `/substack`), and append A1–A5 decisions to `docs/implementation-notes.md`.
  - Modules / files: `deploy/setup.sh`, `docs/PRD.md`, `docs/ARCHITECTURE.md`,
    `docs/implementation-notes.md`.
  - Depends on: P1-T1 (collection name), and ideally P3/P4 landed (accurate docs).
  - Acceptance criteria: spec AC#9, AC#10; ARCH §7 TTL convention.
  - Commit: "chore(deploy): processed_posts TTL + docs (S7)".
  - Status: Todo

---

## Dependency Order
1. **P1-T1** — publications + processed-posts repos *(start here)*
2. **P2-T1** — Substack client *(parallelizable with P1-T1)*
3. **P3-T1** — section compute + formatter + pipeline factory *(needs P1-T1, P2-T1)*
4. **P3-T2** — scheduled worker + enqueue + wiring *(needs P3-T1)*
5. **P4-T1** — `/substack` command *(needs P3-T1, P1-T1)*
6. **P4-T2** — admin CRUD + panel *(needs P1-T1)*
7. **P5-T1** — TTL + docs *(needs P1-T1; do last for accurate docs)*

## Recommended Next Step
- **Start with: P1-T1 — Publications + processed-posts repositories.**
- **Why first:** the `Repositories` bundle is threaded through the pipeline, the
  command handler, and the admin API — every later ticket depends on these two repos
  existing. It is a leaf with no upstream dependency, is fully testable against the
  Firestore emulator in isolation, and locks the doc shapes (slug, cursor-on-add,
  `expire_at`) that P2–P4 build on. P2-T1 (client) can proceed in parallel if a
  second worker is available and A1 (`feedparser`) is confirmed.

## Deferred / Out of Scope
- Claude summarization of posts (native excerpt only — spec Decision 2).
- Separate Substack channel / per-publication messages / per-channel routing.
- Discord command to manage tracking (admin panel only — spec Decision 3).
- Historical archive backfill; selectable `/substack` window beyond default.
- Full-text ingestion, comment counts, paywalled-content access, author avatars.
- Generic (non-Substack) RSS sources — a later generalization of the client.

## Update Rules
After each implementation pass:
- Update ticket **Status** only: Todo / In Progress / Complete / Blocked.
- Update **Current Status** (phase, ticket) and the next recommended ticket.
- Record blockers briefly.
- **One ticket = one git commit** (project CLAUDE.md); log any off-spec
  decision/tradeoff in `docs/implementation-notes.md`.
- Run lint + relevant tests **before** each commit.
- Do **not** add new scope unless `docs/spec.md` changes.
