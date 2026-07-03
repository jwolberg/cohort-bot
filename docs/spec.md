# Feature Spec — Substack Publication Tracking

> Status: draft for review · Author: spec pass · Date: 2026-07-02
> Extends the existing GitHub digest bot with a second content source
> (Substack), reusing the source/store/digest/admin patterns already in the repo.

## Feature Request

- **Original request:** Add Substack data as a tracked source. Substack publications
  expose public RSS/Atom feeds (`https://<publication>.substack.com/feed`), so no
  scraping or auth is needed. Roughly: a Substack source module (fetch + parse
  feeds, like `app/github`), a `tracked_publications` store + admin management,
  new-post dedup (same pattern as `processed_commits`), and posts folded into the
  digest and/or a `/substack` command.
- **Decisions locked (2026-07-02):**
  1. **Delivery:** new posts appear as a **Substack section in the daily digest**
     *and* via an on-demand **`/substack`** command.
  2. **Rendering:** **native feed excerpt only** — title + link + the feed's own
     description/subtitle. **No Claude summarization** (no LLM call, zero added cost).
  3. **Management:** **admin panel only** — publications are managed through
     `/admin/api/publications` + the static panel. No Discord tracking command.

## Problem Statement

- **What it solves:** The bot already answers "what did our tracked developers ship
  today?" from GitHub. It cannot answer "what did our tracked writers publish?"
  Following Substack writers means manually checking each publication or relying on
  email. The PRD explicitly names *"extensible architecture for additional content
  sources"* as a goal; this is the first additional source.
- **Who experiences it:** The same audience as the GitHub digest — engineering
  managers, founders, community/Discord operators — who want one daily surface for
  what the people/orgs they follow are producing, writing included, not just code.
- **Why it matters:** Substack is a primary distribution channel for the technical
  writers this audience follows. A code-only digest misses half the signal, and
  adding a second source proves the extensibility claim with a low-risk, no-auth,
  no-scraping integration (public RSS/Atom).

## Target User and Workflow

- **Primary user (consumer):** members of the Discord where the digest is posted.
- **Primary user (operator):** an admin who curates which publications are tracked.
- **Current workflow:** admin has no way to add a publication; consumers get GitHub
  activity only.
- **Desired workflow:**
  - *Operator:* opens the admin panel → **Publications** → pastes a Substack feed or
    publication URL → it appears in the tracked list; can remove it later.
  - *Consumer (scheduled):* in the daily digest, after the GitHub sections, sees
    **one 📰 message per publication** that has new posts since its last cursor —
    *📰 Publication — N new post(s)*, each post as *"Title"* + link + native excerpt.
    Publications with nothing new post nothing (A3: per-publication fan-out).
  - *Consumer (on-demand):* runs **`/substack`** to see recent posts across all
    tracked publications within a window (**default 1 day, customizable** via a
    `window` option per A2); read-only, no dedup, no cursor advance.

## Success Condition

The feature is successful when:

1. An admin can add/remove/list Substack publications via the admin panel, and the
   set persists in Firestore.
2. The daily scheduled digest posts **one message per publication** with new posts,
   containing **only posts not previously reported** (per-publication dedup + cursor
   advance, retry-safe); publications with nothing new post nothing.
3. `/substack` returns a current, correctly formatted list of recent posts on demand
   (default 1-day window, customizable).
4. A single broken/unreachable/malformed feed never fails the digest run or the
   command — it is skipped and logged (best-effort parity with the GitHub client).
5. No new secret, no new IAM role, and no scraping are introduced (public feeds only).

## Scope

### In scope (v1)

- `app/substack/client.py` — async fetch + RSS/Atom parse → typed `PostRef` objects.
- `TrackedPublicationsRepo` + `ProcessedPostsRepo` added to the `Repositories` bundle.
- Digest integration: per-publication fan-out in the scheduled path (one message per
  publication with new posts, dedup + cursor) and an on-demand path for `/substack`
  (no dedup).
- `/tasks/substack/publication` OIDC worker endpoint, enqueued once **per enabled
  publication** per daily run (mirrors `/tasks/digest/user`).
- `/admin/api/publications` CRUD + a Publications section in the static admin panel.
- `SUBSTACK_COMMAND` in `commands.py`, dispatched via the existing slow-command path.
- `deploy/setup.sh`: TTL policy on the new `processed_posts` collection.
- Tests mirroring `test_github_client.py` (respx), `test_repositories.py` (emulator),
  `test_digest_pipeline.py` (fakes), `test_admin_api.py` (ASGITransport).

### Out of scope (v1 — explicitly deferred)

- **Claude summarization of posts** (decision: native excerpt only). The
  `ClaudeSummarizer` is untouched.
- Per-publication Discord subscriptions, per-channel routing, or a separate Substack
  channel — posts go to the **same** `digest_channel_id`.
- A Discord command to *manage* tracking (admin panel only).
- Full-text ingestion, comment counts, paywalled-content access, author avatars.
- Non-Substack RSS sources (the client targets Substack-style feeds; generic RSS is a
  later generalization).
- Backfilling a publication's historical archive (see cursor-on-add edge case).

## Constraints

### Technical

- **Feed parsing (DECIDED 2026-07-02 — A1: stdlib + `defusedxml`):** Substack
  publishes well-formed **RSS 2.0**. Parse with `defusedxml.ElementTree.fromstring`
  (XML-bomb / entity-expansion hardened — feeds are untrusted remote input, esp.
  custom domains), normalize `pubDate` via stdlib `email.utils.parsedate_to_datetime`
  (RFC-822 → tz-aware UTC), read `dc:creator` via the standard namespace, and clean
  excerpts with stdlib `html.parser` (strip tags, unescape entities, truncate ~280).
  Fetch bytes with the async `httpx` client, cap the response size, then parse.
  *Rejected:* `feedparser` (its Atom / exotic-date / malformed-feed robustness is
  unused at Substack-only scope and adds dependency surface against the lean-deps
  rule); pure stdlib `xml.etree` (leaves XML-bomb defense to hand-rolled guards).
  `defusedxml` is the one justified new dependency. Record in
  `docs/implementation-notes.md` at P2-T1.
- **Excerpt cleaning:** feed descriptions are HTML. Strip tags + unescape entities
  (stdlib `html` + a minimal parser/regex) and truncate (~280 chars).
- **Firestore doc ids** cannot contain `/`; reuse the `_encode()` convention. Slug =
  feed host (e.g. `pragmaticengineer.substack.com` or a custom domain host).
- **Client shape must mirror `GitHubClient`:** async context manager, injectable
  `httpx.AsyncClient` for tests, typed errors, bounded concurrency, best-effort
  per-feed failure.
- **Idempotency:** scheduled path must **post first, then record dedup keys + advance
  cursor** — a Cloud Tasks retry after a failed post must recompute and repost.

### Product

- One daily cadence, one channel — Substack rides the existing digest schedule and
  `digest_channel_id`; no new scheduler job, no new config knob.
- `/substack` is open (read-only), consistent with `/digest`, `/repo`, `/user`.

### Existing-pattern constraints (must reuse, not reinvent)

- Repos bundled in `Repositories`, constructed by `get_repositories()`.
- `gh_factory`-style injectable client factory on the pipeline (`substack_factory`).
- Slow-command defer → `/tasks/followup` → `run_followup` → `edit_original_response`.
- Admin CRUD via `require_admin` (verified IAP or bearer), `Depends(get_repos)`.
- OIDC worker auth via `require_oidc` on `/tasks/*`.
- Formatter/embed helpers in `app/digest/formatter.py` + `app/discord/responses.py`
  (respect Discord's 25-field / ~6000-char embed limits via existing pagination).

## Edge Cases

- **First add must not flood:** a newly added publication has no cursor. Set
  `last_cursor = <add time>` on add so the scheduled digest reports only posts
  published *after* it was added — never the entire back catalog. (Decision; note it.)
- **Unreachable / 404 / non-XML / malformed feed:** skip that publication, log a
  warning, continue — never 500 the run or fail the command.
- **Paywalled excerpt** (e.g. "This post is for paid subscribers"): still show title +
  link; the excerpt is whatever the feed provides. Acceptable for v1.
- **Edited post re-published** with a new date: dedup by the feed entry's stable `id`
  / `guid` (fallback: link URL), so edits do not re-post.
- **Missing entry id:** fall back to the entry `link`; if both missing, skip the entry.
- **Duplicate publication add / re-add:** idempotent — re-enable, preserve
  `created_at`/`last_cursor` (mirror `TrackedUsersRepo.add`).
- **Custom-domain publications** (not `*.substack.com`): accept any feed URL; derive
  slug from host. Store the resolved `feed_url` explicitly.
- **No publications tracked:** scheduled path enqueues no publication tasks;
  `/substack` returns an empty-state embed.
- **Timezone:** normalize all feed dates to timezone-aware UTC before cursor compares.

## Acceptance Criteria

1. `POST /admin/api/publications {feed_url}` normalizes the URL, stores a
   `tracked_publications/{slug}` doc `{slug, feed_url, title, enabled, added_by,
   created_at, last_cursor=<add time>}`, and is idempotent on re-add.
2. `GET /admin/api/publications` lists enabled publications; `DELETE
   /admin/api/publications/{slug}` removes one. All three require admin auth
   (unsigned IAP email header alone is rejected — regression parity with users).
3. The static admin panel renders a Publications section that can add/list/remove.
4. `SubstackClient.fetch_posts_since(feed_url, since)` returns `PostRef`s strictly
   newer than `since` (or all recent, when `since` is None), newest-first, with
   title, url, author, published (UTC), post_id, and a cleaned excerpt.
5. The scheduled digest run enqueues **one `/tasks/substack/publication` task per
   enabled publication**; each worker posts a per-publication message containing only
   posts whose id is **not** in `processed_posts`, then records those ids and advances
   that publication's cursor — **only after** the Discord post succeeds. A publication
   with no new posts posts nothing and does not advance its cursor.
6. Re-running the scheduled path with no new posts produces **no** Substack post and
   no cursor change (idempotent, per publication).
7. `/substack` returns recent posts across enabled publications within a window that
   **defaults to 1 day and is customizable** via a `window` option, **without** dedup
   or cursor advance.
8. A feed that 404s / times out / returns invalid XML is skipped with a logged
   warning; other publications still process.
9. `processed_posts` docs carry `expire_at` and are covered by a TTL policy created in
   `deploy/setup.sh`.
10. No new required env var, secret, IAM role, Cloud Tasks queue, or scheduler job.

## Implementation Outline

Minimal approach: mirror each GitHub-source layer for publications; skip the
summarizer. Suggested build units (one commit each, per repo convention):

- **S1 — Store.** Add `TrackedPublicationsRepo` (`tracked_publications`) and
  `ProcessedPostsRepo` (`processed_posts`, `expire_at` TTL, doc id `slug@post_id`) to
  `app/store/repositories.py`; add both to the `Repositories` dataclass and
  `get_repositories()`. Tests in `test_repositories.py` (emulator).
- **S2 — Client.** `app/substack/client.py`: `SubstackClient` (async CM, injectable
  httpx client, typed `SubstackError`/`NotFoundError`), `PostRef` dataclass,
  `fetch_posts_since(feed_url, since, *, limit=...)`, excerpt cleaner, URL→slug
  normalization helper. Parse via `defusedxml` + `email.utils` + `html.parser` (A1,
  see Constraints). Tests via `respx` against feed fixtures (RSS + malformed).
- **S3 — Digest integration.** Add `PublicationSection` dataclass and
  `compute_publication_section(client, publication, since, *, dedup)` to
  `app/digest/pipeline.py`; extend `DigestPipeline.__init__` with a
  `substack_factory` (mirroring `gh_factory`). `run_fanout()` also enqueues one
  publication task **per enabled publication**; new `process_publication(slug)`
  implements post-first-then-record-then-cursor (mirrors `process_user`). Formatter:
  `format_publication_section(...)` in `app/digest/formatter.py` (one embed per
  publication). Tests in `test_digest_pipeline.py` with fakes.
- **S4 — Worker + enqueue.** `SUBSTACK_PUBLICATION_PATH =
  "/tasks/substack/publication"` and `enqueue_substack_publication()` in
  `app/tasks/queue.py` (reuse the `digest-fanout` queue); route in
  `app/discord/interactions.py` guarded by `require_oidc`; wire the handler in
  `install_digest()`.
- **S5 — `/substack` command.** `SUBSTACK_COMMAND` (optional `window` string option)
  in `app/discord/commands.py`, appended to `COMMANDS`; add `"substack"` to
  `SLOW_COMMANDS` and a `run_followup` branch calling the on-demand
  (`dedup=False`) path → `edit_original_response`. Register via
  `scripts/register_commands.py`. Update `HELP_TEXT`.
- **S6 — Admin panel.** `/admin/api/publications` GET/POST/DELETE in
  `app/admin/api.py` (`Depends(require_admin)`, `Depends(get_repos)`); Publications
  section in `app/admin/static/index.html` (Alpine.js rows). Tests in
  `test_admin_api.py` (ASGITransport, `dependency_overrides`).
- **S7 — Deploy + docs.** `deploy/setup.sh`: enable TTL on
  `processed_posts.expire_at`. Update `.env.example` only if a dependency/setting is
  actually added (none expected). Update `docs/PRD.md` + `docs/ARCHITECTURE.md`
  (§7 collections, §5 commands) and append decisions to
  `docs/implementation-notes.md`.

### Likely components / systems involved

Firestore (2 new collections), Cloud Tasks (reuse `digest-fanout`), Cloud Scheduler
(unchanged — folds into `/tasks/digest/run`), the FastAPI admin router, the Discord
interactions/handlers slow path, and the digest pipeline/formatter.

## File Impact Guess (estimate only)

**New:**
- `app/substack/__init__.py`, `app/substack/client.py`
- `tests/test_substack_client.py`

**Modified:**
- `app/store/repositories.py` (2 repos + bundle)
- `app/digest/pipeline.py` (section compute, `substack_factory`, worker handler, wiring)
- `app/digest/formatter.py` (substack section formatter)
- `app/tasks/queue.py` (path const + `enqueue_substack_check`)
- `app/discord/interactions.py` (new `/tasks/substack/publication` route + handler setter)
- `app/discord/commands.py` (`SUBSTACK_COMMAND`)
- `app/discord/handlers.py` (`SLOW_COMMANDS`, `run_followup` branch, `HELP_TEXT`)
- `app/admin/api.py` (publications CRUD)
- `app/admin/static/index.html` (Publications UI)
- `deploy/setup.sh` (TTL for `processed_posts`)
- `scripts/register_commands.py` (auto-picks up `COMMANDS`; verify)
- `pyproject.toml` / requirements (`defusedxml` — the one net-new dependency)
- `tests/test_repositories.py`, `tests/test_digest_pipeline.py`, `tests/test_admin_api.py`
- `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/implementation-notes.md`

## Validation Plan

- **Unit — client:** `respx`-mocked RSS, Atom, and malformed feeds → assert `PostRef`
  fields, `since` filtering, excerpt cleaning, and best-effort skip on bad feeds.
- **Unit — store:** Firestore emulator → add/re-add idempotency, cursor get/set,
  `processed_posts` has/record + `expire_at` presence.
- **Unit — pipeline:** fake client/rest/repos → scheduled path dedups + advances
  cursor only after a successful post; a post failure leaves cursor/dedup unchanged;
  on-demand path skips dedup.
- **Unit — admin API:** ASGITransport + `dependency_overrides` → CRUD happy paths +
  auth rejection (including the unsigned-IAP-email regression).
- **Lint + full test suite** before each S# commit (repo convention).
- **Manual e2e (staging):** add a real public Substack feed via the panel, trigger a
  digest run (admin "test digest" analog), confirm a correctly deduped per-publication
  message posts; run `/substack`; confirm a second run with no new posts is silent.

## Open Questions (resolve before or during S1)

1. ~~**`feedparser` vs stdlib**~~ — **RESOLVED 2026-07-02:** stdlib `defusedxml` +
   `email.utils` + `html.parser`. `defusedxml` is the only net-new dependency.
2. ~~**`/substack` window**~~ — **RESOLVED 2026-07-02 (A2):** default **1 day**,
   **customizable** via a `window` option (e.g. `1d`|`7d`|`30d` choices).
3. ~~**Substack section placement**~~ — **RESOLVED 2026-07-02 (A3):**
   **per-publication messages** (one message per publication with new posts), fanned
   out like GitHub's per-user digest. No separate combined section/header.
