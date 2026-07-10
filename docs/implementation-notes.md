# Implementation Notes

Running log of decisions, deviations, and tradeoffs made while executing the
[GitHub Digest Discord Bot plan](plans/2026-07-02-001-feat-github-digest-discord-bot-plan.md).
Dated, tied to the implementation unit (U-ID) being worked.

## 2026-07-10 — Linked repo titles in digest embeds

Repo names in digest embeds now link to the repo on GitHub, matching how the
username already links to the profile.

- **Link lives in the field value, not the field name (Discord constraint):**
  Discord renders masked links (`[text](url)`) in embed descriptions and field
  values, but not in field names or titles. The per-user embed keys each field
  by repo name, so the clickable `[**owner/repo**](https://github.com/owner/repo)`
  leads the field value instead. The repo name therefore appears twice per
  field — plain as the header, linked as the first body line. User chose this
  over moving the repos into the description, which would have linked the
  header itself at the cost of the field layout.
- **Batched `/digest` needed no layout change** — `_repo_lines` already rendered
  repo names inside a field value, so the bold name simply became a masked link.
- **Truncation caveat:** `_repo_lines` still hard-slices at 1024 chars, so a
  user with many repos can have a trailing link cut mid-URL (it would render as
  raw text). Pre-existing behavior (it could already cut mid-`**bold**`); left
  alone to keep the change small.

## 2026-07-08 — Tracked-user groups (cohort vs. AI leaders)

Split tracked GitHub users into two fixed groups — `cohort` and `leaders` — each
with its own admin list and its own Discord channel on the same server.

- **Two fixed groups, not N (user decision):** kept it to exactly `cohort` +
  `leaders` (constants `GROUPS`/`DEFAULT_GROUP` in `repositories.py`) rather than
  a general groups collection. Smallest change that meets the request; a third
  group later is a code change, which is acceptable per current scope.
- **Backward compatible by default:** `group` is a new field on
  `tracked_users` docs, defaulting to `cohort`. Legacy docs (no field) and any
  caller that omits a group — including Discord `/track add` — read as `cohort`,
  so no data migration is needed. `list_enabled`/`list_all` normalize the group
  on read via `_with_group`.
- **Channel per group in config:** the cohort channel stays under the original
  `digest_channel_id` key (backward compat); the leaders channel is a new
  `leaders_channel_id`. `channel_for_group(config, group)` resolves the mapping;
  both keys are now editable from the admin panel.
- **Fan-out routing:** `run_fanout` partitions users by group and posts one
  header per non-empty group to that group's channel. A group with **no**
  configured channel is skipped (logged `digest_group_no_channel`) and its users
  are not enqueued — avoids posting nowhere. `process_user` reads the user's
  stored group and resolves the channel from it (this also removed a redundant
  Firestore read — it now reads the user doc once instead of a separate
  `get_cursor`).
- **Re-add moves a user between groups:** re-adding an existing user updates its
  `group` (still preserves `created_at`/`last_cursor`), so reassignment needs no
  separate endpoint.
- **Out of scope:** Substack publications remain a single list posting to the
  cohort/default channel; `/track` gained no group option (admin panel manages
  groups). Follow-up if wanted: a `group` option on `/track add`.
- **Validation:** full suite green (157 passed) against the Firestore emulator;
  `ruff` clean on all changed files (3 remaining ruff warnings are pre-existing
  in untouched files: `app/github/client.py`, `tests/test_commands.py`).

## 2026-07-03 — Substack publication tracking (S1–S7)

Implemented the second content source (Substack) per `docs/spec.md` +
`docs/BUILD_PLAN.md`. Tickets S1–S6 complete; S7 = TTL wired, PRD/ARCHITECTURE
doc updates still pending.

- **S1 store:** `TrackedPublicationsRepo` + `ProcessedPostsRepo` mirror the
  GitHub repos. Decision (A5): a publication's `last_cursor` is set to the add
  time via `SERVER_TIMESTAMP` on `add()`, so the scheduled digest never dumps a
  back catalog. `processed_posts` doc id = `slug@post_id`; the post_id (a feed
  guid/link, often a URL) is `_encode()`d, same as `owner/repo`.
- **S2 client:** `app/substack/client.py` mirrors `GitHubClient`. `defusedxml`
  is the one net-new runtime dependency (A1) — added to `pyproject.toml`. Dedup
  key = `guid`, fallback `link`; entries missing a parseable `pubDate` OR both
  keys are skipped. Excerpts are native (no LLM). Best-effort: 404 →
  `NotFoundError`, any other transport/parse failure → `SubstackError`; callers
  skip one bad feed rather than failing the run/command.
- **S3 pipeline:** `compute_publication_section` mirrors `compute_section`
  (dedup on scheduled path, none on-demand; cursor advances past everything
  fetched). `process_publication` is post-first-then-record-then-cursor
  (retry-safe). `run_fanout()` now also enqueues one task per enabled
  publication; the SLO heartbeat log gains `publications`/`pubs_enqueued`
  fields but its emit condition is unchanged (still keyed off the GitHub header
  — GitHub remains the primary source). `substack_factory` mirrors `gh_factory`.
- **S4 worker:** `/tasks/substack/publication` (OIDC-gated) reuses the
  `digest-fanout` queue — no new queue/scheduler (AC#10).
- **S5 `/substack`:** optional `window` option (`1d`|`7d`|`30d`, default `1d`
  per A2) via the existing slow-command defer → follow-up → PATCH path.
- **S6 admin:** `/admin/api/publications` CRUD + a Publications panel section.
  Decision: the admin POST normalizes any pasted host/publication/feed URL to
  `https://<host>/feed` (Substack-style feeds live at `/feed`); a custom domain
  whose feed sits at a different path is out of scope (spec: Substack-style
  only). `title` is optional on add and falls back to the slug in rendering — a
  best-effort channel-title fetch was deliberately skipped to avoid an extra
  add-time network dependency and keep the client surface small.
- **S7 deploy/docs:** `deploy/setup.sh` enables the `processed_posts`
  `expire_at` TTL (AC#9). `docs/PRD.md` (Newsletter Intelligence → marked
  Implemented, corrected to admin-panel management + native excerpt; `/substack`
  added to Slash Commands) and `docs/ARCHITECTURE.md` (§6b/§6c flows, §7
  collections: `tracked_publications` + `processed_posts`) updated to match the
  shipped feature.
- **Deployed:** production revision `digest-bot-00008-g8g` (image `:4cc6bb2`),
  2026-07-03. `/tasks/substack/publication` + `/admin/api/publications` verified
  live. **Manual step remaining:** register the `/substack` slash command with
  Discord via `scripts/register_commands.py` (needs the live Discord token).

## 2026-07-07 — `/publication` command (Substack analog of `/repo`)

- **Feature:** a new `/publication <url_or_host>` slash command that inspects
  *any* Substack publication live (tracked or not), mirroring how `/repo`
  inspects any GitHub repo. Returns a public embed with the publication title,
  description, and up to 5 recent posts. User-requested parity with `/repo`.
- **Client:** added `SubstackClient.fetch_publication()` returning a new
  `PublicationView` (slug/title/description/link/posts). This is the first time
  we parse the feed's `<channel>` title/description — S6 had deliberately
  skipped channel-title fetching, but here it's on the live-inspection path
  (no add-time network cost concern), so we parse it. Refactored the shared
  transport/size/XML error contract out of `fetch_posts_since` into a private
  `_fetch_channel()` + `_parse_items()` so both methods share one code path
  (no behavior change to `fetch_posts_since`; existing tests still pass).
- **Shared normalizer:** moved `_normalize_feed_url` out of `app/admin/api.py`
  into `app.substack.client.normalize_feed_url` (public) so the admin panel and
  the command share one implementation. Same logic; `/publication` validates +
  normalizes the pasted URL in `dispatch()` before deferring (mirrors the
  `_REPO_RE` guard for `/repo`). Note: normalization only rejects empty/hostless
  input — a bad-but-hostful URL still defers and then 404s/errors in the worker,
  rendered as a friendly "Not found"/"Unreachable" embed (same as `/repo`).
- **Handler:** `/publication` is a slow command; unlike `/substack` (which
  delegates to the digest-pipeline provider over the *tracked* set), it follows
  the `/repo` pattern — opens a `SubstackClient` directly in `run_followup` via
  a new `substack_factory` (parallel to `gh_factory`) and builds the embed with
  the reused `formatter._post_field`.
- **Manual step remaining:** re-run `scripts/register_commands.py` (needs the
  live Discord token) to register `/publication` with Discord — nothing else
  auto-registers it.

## 2026-07-03 — Digest signal filtering (chores/docs/fixes excluded)

- **Change:** the daily digest was too long, so `compute_section`
  (`app/digest/pipeline.py`) now drops **low-signal commits** — chores,
  documentation updates, and bug fixes — before grouping/summarizing. Applies to
  both the scheduled per-user digest and the on-demand `/digest` view (shared
  code path).
- **Classifier (`_is_low_signal`):** primary detection is the
  conventional-commit type prefix (`chore`/`docs`/`fix` + aliases
  `doc`/`bugfix`/`hotfix`); a small start-of-subject keyword fallback catches
  non-conventional messages (`Fix …`, `Update README`, `Bump …`, `typo`). Only
  the first subject line is inspected, and keywords match only at the start so
  feature commits that merely mention these words are kept
  (e.g. "Improve docs discoverability", "fixture: seed data").
- **Cursor semantics (deliberate):** the cursor still advances past the **whole
  fetched batch** (including filtered commits) so noise isn't re-scanned each
  run. Filtered SHAs are **not** recorded in `processed_commits` (they're never
  reported), which is harmless because the cursor already guards re-fetching.
- **Tradeoffs / assumptions:** (a) scope limited to the three requested
  categories — `ci`/`build`/`style`/`test` are *not* filtered; extend
  `_LOW_SIGNAL_TYPES`/`_LOW_SIGNAL_PREFIXES` to change that. (b) A subject that
  *leads* with a fix keyword but also adds a feature (e.g. "Fixes #12 add dark
  mode") is dropped — accepted, message-led intent wins. (c) If *all* of a
  user's commits are low-signal, the section is omitted and the cursor is not
  advanced (matches existing "no new activity" behavior).
- **Pre-existing failure (not mine):** `test_on_demand_builds_batched_embeds`
  fails on `main`/HEAD too — its event is hardcoded to `2026-07-02` while
  `today` is `2026-07-03`, so the commit falls outside the "today" window. Left
  as-is (out of scope); flagging for a follow-up to make the test date-relative.

## 2026-07-02 — First production deploy (pass 1)

### Deploy: infra provisioning against `cohort-bot-1`
- **Target project:** created a **new dedicated project `cohort-bot-1`** (user's
  choice) rather than reusing the shell's then-current project (an existing
  personal project), which already hosts another app + a live Firestore Native
  DB. Billing linked to an existing account (`<redacted>`).
- **Scope:** ran `deploy/setup.sh` **pass 1 only** (no `SERVICE_URL`) — infra
  now exists (APIs, `digest-bot-sa` + least-priv IAM, Artifact Registry
  `digest-bot`, Firestore Native @ us-central1, TTL policy, Cloud Tasks queues
  `digest-fanout`/`interaction-followups`, 5 empty Secret Manager secrets).
  Stopped before deploy/Scheduler because the 5 credentials aren't in hand yet.
- **Admin auth decision:** will use the `ADMIN_TOKEN` bearer fallback (no IAP/LB)
  for this MVP — leave `IAP_AUDIENCE` unset.
- **Deviation — `setup.sh` hardening:** the first run silently redirected the
  Cloud Tasks step to a **different (personal) project** — a concurrent
  gcloud process flipped the shared `gcloud config set project` value mid-run.
  Changed `setup.sh` to pin the project **per-process** via
  `export CLOUDSDK_CORE_PROJECT` (env var, takes precedence over and is immune to
  the shared mutable config) instead of `gcloud config set project`. No resources
  were created in the wrong project (the misdirected call failed on a disabled
  API). Re-ran clean.

### Deploy: build & first deploy (Step 3)
- **Deviation — `cloudbuild.yaml` image tag:** the config tagged the image with
  the built-in `${SHORT_SHA}`, which is **only populated for trigger-based
  builds**. DEPLOY.md instructs a manual `gcloud builds submit`, where
  `SHORT_SHA` is empty → invalid image ref (`…/digest-bot:`) → build fails.
  Replaced with a `_TAG` user substitution (default `manual`), passed explicitly
  at submit time (git short sha). Fixes every manual deploy.
- **Placeholder secrets (to unblock Discord-path testing):**
  - `GITHUB_TOKEN` = `PLACEHOLDER_replace_with_real_github_pat` — the field is
    required (app won't boot without *a* value) but unvalidated, so a placeholder
    boots the service. GitHub-backed commands 401 until a real PAT is set. User
    hadn't provided a PAT ("all the test server info" covered Discord/GCP, not
    GitHub).
  - `ANTHROPIC_API_KEY` v1 is **suspect** (218 chars, no `sk-ant-` prefix) —
    kept for boot; summarizer will fail until re-stored. Flagged to user.
- **Test env documented** in `docs/test-environment.md` (project, Discord app
  `TV1-dev`/`1222953770963570831`, two test guilds/channels, secret status).
- Admin auth: `ADMIN_TOKEN` bearer (no IAP) per earlier decision — still to be
  set post-deploy.
- **Deployed & validated (LIVE):** build+deploy succeeded (revision 00002).
  - **Public ingress:** the org blocked deploy-time `--allow-unauthenticated`;
    user granted `allUsers` `run.invoker` manually (Option A). Requests then
    reached the app.
  - **`/healthz` GFE shadow:** Google's frontend intercepts the literal
    `/healthz` path on `*.run.app` and returns its own 404 before the container;
    every other route works (`/`→app-JSON-404, `/interactions`→405, `/admin/`,
    `/docs`, `/openapi.json`→200). Route *is* registered. Cost ~most of the
    deploy debugging. **Follow-up: rename the health route to `/health`.**
  - **Step 4/5 done:** `SERVICE_URL`+`TASK_INVOKER_SA_EMAIL` set; Scheduler
    `daily-digest` + `digest_heartbeat` metric created.
  - **Discord wired:** `TV1-dev` already in guild `<redacted>`; 6
    commands registered to it; Interactions Endpoint URL set via API → PING→PONG
    HTTP 200 (Ed25519 verified end to end).
  - **Still needed for full function:** real `GITHUB_TOKEN` (placeholder now),
    fix suspect `ANTHROPIC_API_KEY`, set `ADMIN_TOKEN` + bootstrap
    `config.digest_channel_id` (Step 9), monitoring alert policy (Step 10).
- **Post-deploy runtime fixes (during testing):**
  - All 5 real secrets loaded & validated (Discord ✓, `GITHUB_TOKEN` v2 auth,
    5000/hr rate limit confirmed, `ANTHROPIC_API_KEY` v2 valid, `ADMIN_TOKEN`
    set). Secrets pinned to explicit versions to force revision remounts.
  - 5 GitHub users tracked via admin API; one username corrected from a 404
    typo.
  - `config.digest_channel_id` = `<redacted>` (automated digest target).
    On-demand `/digest`/`/repo`/`/user` reply in the invoking channel via
    `edit_original_response` — no channel config needed.
  - **Deviation — `setup.sh` missing `actAs`:** slow commands enqueue a Cloud
    Task with an OIDC token minted for `digest-bot-sa`; creating it needs
    `iam.serviceAccounts.actAs` on that SA. `setup.sh` granted only project-level
    roles, so `create_task` returned `PERMISSION_DENIED` and every deferred
    command ("didn't respond in time"). Fixed `setup.sh` to grant the SA
    `roles/iam.serviceAccountUser` on itself; live grant applied separately.

### Bug fix — empty digests (GitHub Events API drift)
- **Symptom:** `/digest today` returned "No activity to report" even for users
  who pushed within the window.
- **Root cause:** GitHub's `/users/{u}/events/public` **no longer inlines the
  `commits[]` array** in PushEvent payloads — it now returns only the
  `before`/`head` SHA range (+`repository_id`). `fetch_user_commits_since` read
  `payload.commits`, always got `[]`, so no commits were ever surfaced. Confirmed
  the stripped payload across auth schemes/API versions (not an auth issue).
- **Fix:** `app/github/client.py` — new `_commits_from_push()` hydrates each
  push via `GET /repos/{repo}/compare/{before}...{head}`; new-branch pushes
  (`before` all-zeros) fall back to the single head commit; unresolvable ranges
  (deleted/renamed repo) are skipped best-effort. Push time is kept as each
  commit's timestamp to preserve window/cursor semantics.
- **Tests:** updated `test_github_client.py` (compare mocks) + added new-branch
  and 404-skip cases; updated `test_digest_pipeline.py::_push_event` to emit
  before/head and register compare mocks. Full suite 86 passed. Deployed.
- **Scope note:** Events API is **public-only** — private-repo pushes remain
  invisible by design (user confirmed public repos are the target).

### Session setup
- Executing the full MVP (U1–U12) serially on branch
  `feat/github-digest-discord-bot`, committing per unit.
- **Environment constraint:** no live GCP project, Discord app, or API
  credentials here. Building all code + emulator/mock test suite (U1–U10, U12)
  and deploy scaffolding (U11); live deploy / Discord registration / real
  interaction-endpoint smoke test are left for the user to run with real creds.
  This matches the plan's deferred live-validation posture.
- Package manager: **uv** (present locally); Python 3.12.
- Installed the `cloud-firestore-emulator` gcloud component for U2/U9/U10
  integration tests.

### Code review (post-build, adversarial) — outcomes
Ran a code-reviewer pass over the auth/idempotency-critical files. Five findings
confirmed; four fixed, one accepted as a documented tradeoff.
- **[Critical, fixed] IAP header spoofing** (`admin/api.py`): `require_admin` no
  longer trusts the unsigned `X-Goog-Authenticated-User-Email` header. It now
  verifies the signed `X-Goog-IAP-JWT-Assertion` against Google's IAP public
  keys for the configured `IAP_AUDIENCE`; bearer token remains the local-dev
  fallback.
- **[High, fixed] OIDC caller identity** (`tasks/auth.py`): `require_oidc` now
  also checks the token's `email` claim equals `task_invoker_sa_email` — a
  valid-audience token from an unrelated Google account no longer passes.
- **[High, NOT changed — documented tradeoff] duplicate report on crash between
  post and record** (`digest/pipeline.py process_user`): posting before
  recording SHAs/cursor is deliberate (the plan prioritizes "recover gracefully
  after downtime" and no-loss). Recording before posting would instead *lose* a
  report if the post then failed — strictly worse. A crash in the post→record
  window yields an at-least-once **message** dup (rare); commit-level dedup via
  `processed_commits` still bounds duplicate *reporting* to <1% (the PRD metric).
  True exactly-once would need a transactional outbox / Discord idempotency key —
  out of MVP scope.
- **[Medium, fixed] duplicate header on partial-failure retry** (`run_fanout`):
  per-user enqueue is now best-effort (catch `EnqueueError`, log, continue) so
  one enqueue failure can't 500 the job and make Cloud Scheduler retry (which
  would re-post the header).
- **[Medium, fixed] same-second events silently dropped** (`github/client.py`):
  the cursor lower bound is now inclusive (`created < since` to stop paging), so
  a distinct later push sharing the cursor's 1-second timestamp is re-included;
  SHA dedup filters ones already reported.
- Ruled out by the reviewer (no change): `_encode` doc-id collisions (GitHub
  owners can't contain `_`), `audience=""` OIDC bypass (google-auth enforces
  empty audience → fails closed), Ed25519 verify (correct, constant-time).

### U11 — Deployment scaffolding
- **Verification done locally:** `setup.sh` passes `bash -n`; `cloudbuild.yaml`
  is valid YAML; `uv build` produces a wheel (validates the Dockerfile's
  `pip install .`). Full `docker build`, `setup.sh` execution, and the Cloud Run
  deploy + Discord PING smoke test **could not run here** (no Docker daemon, no
  GCP project) — left for the user with real credentials, per the session's
  stated constraint.
- **Firestore Native** is created explicitly (irreversible); `setup.sh` guards
  every resource for idempotent re-runs.
- **Two-pass deploy:** `setup.sh` (infra) → Cloud Build (deploy, learn URL) →
  `setup.sh` again with `SERVICE_URL` to create the Scheduler job (needs the
  live URL as OIDC audience). README documents the full sequence.

### U10 — Admin panel
- **Auth:** `require_admin` authorizes if the IAP identity header
  (`X-Goog-Authenticated-User-Email`) is present (IAP sets it only on forwarded
  requests) OR a valid `ADMIN_TOKEN` bearer is supplied (local-dev fallback).
  Follow-up/hardening: verify the `X-Goog-IAP-JWT-Assertion` signature rather
  than trusting the email header, in case the service is ever reachable without
  IAP in front.
- **Parity:** admin API and Discord `/track` share the same `Repositories`.
- **Test gotcha:** Starlette's sync `TestClient` uses a fresh event loop per
  request, which breaks the loop-bound async Firestore gRPC channel across
  multi-request tests. Admin API tests drive the app via in-loop
  `httpx.ASGITransport` so all requests share the pytest-asyncio loop.
  Production (single uvicorn loop) is unaffected.

### U9 — Daily digest pipeline
- **Resolved §14 open decision (message shape):** scheduled digest uses
  **per-user fan-out messages** — `/tasks/digest/run` posts one batched header
  ("📊 GitHub Daily Digest — <date>") then enqueues one `/tasks/digest/user`
  task per enabled user; each user task posts its own section message. Rationale:
  each user's work becomes an independently retryable, idempotent Cloud Task with
  **no fan-in/join coordination** — directly serving the ">99% success" and
  "recover gracefully" goals with far less complexity than a batched single
  message (which would need a Firestore join + last-writer-assembles race). The
  **on-demand `/digest` command** does use batched, paginated embeds (matches the
  PRD example header + per-user layout in one interactive response).
- **Idempotency:** per-user task records `processed_commits` SHAs and advances
  `last_cursor` **only after** a successful post. Post failure → Cloud Tasks
  retries; cursor unchanged and SHAs unrecorded, so the retry recomputes and
  reposts. Tradeoff: post-succeeds-but-record-fails yields an at-least-once
  message dup (rare); commit-level dedup still holds via `processed_commits`.
- **Cursor advance:** to the newest commit timestamp seen this run (exclusive
  cursor), so the next run skips them even before SHA dedup.
- **On-demand does not dedup or advance cursors** — it's a read-only window view
  (`compute_section(dedup=False)`), so `/digest today` shows the day's activity
  regardless of what the scheduled digest already reported.

### U5 — GitHub REST client
- **Resolved deferred question (events vs commits):** per-user commit fetch uses
  the **public Events API** (`/users/{u}/events/public`) — one request per user,
  newest-first, early-stop once past the cursor. PushEvent payloads give
  sha/message/author. Cheaper than enumerating repos; matches ARCHITECTURE §8.
  Limitation: Events API caps at ~300 recent events, fine for a daily digest.
- **Cursor boundary is exclusive** (strictly after `last_cursor`); combined with
  `processed_commits` SHA dedup this makes re-runs safe.
- **ETag caching** only on repo metadata (`fetch_repo`); recent commits and
  contributors are small and fetched fresh. `304` returns the cached RepoInfo
  and does not re-write the cache.
- **Branch ahead/behind** is best-effort (compare endpoint), enriched only for
  the first N branches to bound calls — "when available" per PRD.

### U2 — Firestore Native data layer
- **Doc-id encoding:** Firestore ids can't contain `/`, so `owner/repo` is
  encoded as `owner__repo` in `processed_commits` (`owner__repo@sha`) and
  `repo_cache` ids — matches the `{repo__sha}`/`{owner__repo}` path hints in
  ARCHITECTURE §7. The logical key is preserved in the `repo` field.
- **`remove` = hard delete** (not soft-disable). The `enabled` flag is retained
  for future soft-disable and drives `list_enabled`, but `/track remove` removes
  the doc. Satisfies "excluded from list-enabled".
- **TTL:** `processed_commits` docs store an `expire_at = processed_at + 90d`
  field; the actual TTL policy on `expire_at` is created in U11 `setup.sh`.
- **Emulator gotcha (important):** the local gcloud SDK (428) launcher does
  `import imp`, removed in Python 3.12 — so the emulator crashes when spawned
  under the 3.12 test venv. Fixed in `conftest.py` by discovering a pre-3.12
  interpreter and passing it as `CLOUDSDK_PYTHON` to the emulator subprocess.
  The fixture now captures the emulator log to `tests/.firestore-emulator.log`
  and prints it on startup failure. Emulator boots in ~4s once warm.

### U1 — Project scaffolding & configuration
- **Config source:** `app/config.py` reads settings from **env vars only**
  (pydantic-settings). Decision: rather than call the Secret Manager API at
  runtime, secrets are injected as env vars by Cloud Run from Secret Manager at
  deploy time (wired in U11's `setup.sh`). Simpler, one less runtime dependency,
  and testable. Deviation from a literal reading of the plan's "loads from …
  Secret Manager" — same outcome, cleaner mechanism. Noted for U11.
- **Operational config** (digest channel/hour, admin role ids) lives in the
  Firestore `config` singleton (ARCHITECTURE §7), not env — it is
  runtime-editable via the admin panel (U10). `config.py` only holds infra
  values + an optional `DEFAULT_DIGEST_CHANNEL_ID` bootstrap.
- **Router wiring:** `create_app()` attaches the interactions/admin routers via
  guarded `try/except ImportError` so the app boots at any point during the
  build (before U3/U10 exist). Routers are added by their owning units.
- **Test env:** `tests/conftest.py` sets required env defaults at import time so
  `app` imports cleanly during collection, and provides a session-scoped
  Firestore emulator + async client fixture (used from U2 onward).
