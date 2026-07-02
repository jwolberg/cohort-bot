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

Everything runs on Google Cloud Run (scale-to-zero). See **[DEPLOY.md](DEPLOY.md)**
for the full production runbook — prerequisites, the ordered `setup.sh` →
Cloud Build → runtime-env → IAP → Discord sequence, verification checklist, and
troubleshooting. The short version:

1. `bash deploy/setup.sh` — provision infra (creates **Firestore in Native mode**,
   an irreversible one-time choice; SA + least-priv IAM; queues; secrets; TTL;
   heartbeat metric).
2. Set the 5 secret values in Secret Manager.
3. `gcloud builds submit --config deploy/cloudbuild.yaml` — build + deploy.
4. Set `SERVICE_URL` + `TASK_INVOKER_SA_EMAIL` runtime env, then re-run
   `setup.sh` with `SERVICE_URL` to create the Cloud Scheduler job.
5. Front `/admin/*` with IAP, register slash commands, and set the Discord
   Interactions Endpoint URL to `<service-url>/interactions`.
