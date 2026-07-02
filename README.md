# cohort-bot — GitHub Activity Digest Discord Bot

A Python Discord bot on **Google Cloud Run** that tracks selected GitHub users
and posts a daily engineering-activity digest to a Discord channel, plus
on-demand slash commands (`/repo`, `/branches`, `/user`, `/digest`) and an
Alpine.js admin panel.

It uses Discord **HTTP Interactions** (no gateway) so Cloud Run scales to zero,
**Cloud Scheduler** for the daily trigger, **Cloud Tasks** for per-user fan-out
and deferred slow-command work, **Firestore (Native mode)** for persistence, and
**Anthropic Claude** to summarize each user's commits.

See [`docs/PRD.md`](docs/PRD.md) and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for product and technical framing, and
[`docs/plans/`](docs/plans/) for the build plan.

## Requirements

- Python 3.12
- [`uv`](https://github.com/astral-sh/uv) (or pip) for dependency management
- Google Cloud SDK with the Firestore emulator component (for tests):
  `gcloud components install cloud-firestore-emulator`

## Local setup

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
cp .env.example .env   # then fill in real values
```

Run the app locally:

```bash
uv run uvicorn app.main:app --reload --port 8080
curl localhost:8080/healthz   # -> {"status":"ok"}
```

To exercise real slash commands locally, tunnel the port (e.g. `ngrok http
8080`) and set the dev Discord app's *Interactions Endpoint URL* to
`https://<tunnel>/interactions`. Register commands against a test guild for
instant propagation (see `scripts/register_commands.py`).

## Testing

Integration tests for the data layer and digest pipeline run against the
**Firestore emulator**, started automatically by the test fixtures.

```bash
uv run pytest
```

Set `FIRESTORE_EMULATOR_HOST` yourself only if you want to target an
already-running emulator; otherwise the fixture manages one.

## Layout

```
app/
  main.py          FastAPI app + route wiring
  config.py        settings (env / Secret Manager)
  logging.py       structured JSON logging
  discord/         interaction verify, dispatch, commands, handlers, REST
  github/          GitHub REST client
  store/           Firestore client + repositories
  summarizer/      Claude summarizer
  digest/          daily digest pipeline + formatter
  tasks/           Cloud Tasks enqueue + OIDC auth
  admin/           admin JSON API + static Alpine.js panel
scripts/           register_commands.py
deploy/            Dockerfile, cloudbuild.yaml, setup.sh
tests/             pytest suite (emulator + mocks)
```

## Deployment

Everything runs on Google Cloud Run (scale-to-zero). Deploy in three passes.

**1. Provision infrastructure** (idempotent — safe to re-run):

```bash
PROJECT_ID=my-project REGION=us-central1 bash deploy/setup.sh
```

This enables APIs and creates: the `digest-bot-sa` service account with
least-privilege IAM (`datastore.user`, `cloudtasks.enqueuer`,
`secretmanager.secretAccessor`, `run.invoker`), an Artifact Registry repo,
**Firestore in Native mode** (irreversible), a TTL policy on
`processed_commits.expire_at`, the `digest-fanout` + `interaction-followups`
Cloud Tasks queues, empty Secret Manager secrets, and a `digest_heartbeat`
log-based metric.

Set the secret values:

```bash
printf %s "<value>" | gcloud secrets versions add DISCORD_PUBLIC_KEY --data-file=-
# ...repeat for DISCORD_TOKEN, DISCORD_APP_ID, GITHUB_TOKEN, ANTHROPIC_API_KEY
```

**2. Build & deploy** the container (secrets are mapped in as env vars):

```bash
gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions=_REGION=us-central1
```

**3. Finish wiring** — re-run `setup.sh` with the deployed URL to create the
Cloud Scheduler `daily-digest` job (OIDC → `/tasks/digest/run`):

```bash
PROJECT_ID=my-project REGION=us-central1 SERVICE_URL=https://digest-bot-xxxx.a.run.app \
  bash deploy/setup.sh
```

Then, manually:
- Front `/admin/*` with **Identity-Aware Proxy** and grant your account
  `roles/iap.httpsResourceAccessor`.
- Register slash commands:
  `uv run python -m scripts.register_commands --guild <GUILD_ID>`.
- In the Discord Developer Portal, set the **Interactions Endpoint URL** to
  `https://<service-url>/interactions` (Discord sends a PING, expects a signed
  PONG — the service must be deployed first).
- Create a Cloud Monitoring alert that pages when `logging/user/digest_heartbeat`
  reports no data over ~26h (SLO: daily digest posts >99% of days).

Firestore is created in **Native mode** — this choice is made once at database
creation and cannot be changed.
