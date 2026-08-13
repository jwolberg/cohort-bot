#!/usr/bin/env bash
#
# Idempotent provisioning for the GitHub Digest Discord bot.
#
# Usage:
#   PROJECT_ID=my-proj REGION=us-central1 SERVICE_URL=https://... \
#     bash deploy/setup.sh
#
# Re-runnable: each step checks for existing resources before creating them.
# Requires: gcloud (authenticated), a billing-enabled project.
#
# NOTE: Firestore mode is chosen once at database creation and is IRREVERSIBLE.
# This script creates the database in NATIVE mode.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-digest-bot}"
SA_NAME="${SA_NAME:-digest-bot-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO="${REPO:-digest-bot}"
FANOUT_QUEUE="${FANOUT_QUEUE:-digest-fanout}"
FOLLOWUPS_QUEUE="${FOLLOWUPS_QUEUE:-interaction-followups}"
# Daily digest is best-effort: a failed fan-out task (a user/repo/installation
# section) is re-picked on the next run because the cursor only advances after a
# successful post. Cap retries low — the Cloud Tasks default of 100 attempts
# turns a permanent failure into a day-long retry storm. 3 attempts absorb a
# transient blip (cold start, Discord 5xx) without the storm.
FANOUT_MAX_ATTEMPTS="${FANOUT_MAX_ATTEMPTS:-3}"
DIGEST_HOUR_UTC="${DIGEST_HOUR_UTC:-13}"
# SERVICE_URL is the deployed Cloud Run URL; required for Scheduler/Tasks targets
# and the OIDC audience. Deploy once (Cloud Build) to learn it, then re-run.
SERVICE_URL="${SERVICE_URL:-}"

# Pin the project per-process via the env var rather than the shared, mutable
# `gcloud config set project`. The global config can be flipped by a concurrent
# gcloud process mid-run, which would silently redirect commands to the wrong
# project; CLOUDSDK_CORE_PROJECT is process-local and takes precedence.
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"

echo "==> Enabling APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudtasks.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  iap.googleapis.com \
  monitoring.googleapis.com

echo "==> Service account ${SA_EMAIL}"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="GitHub Digest bot"
fi

echo "==> Least-privilege IAM"
for role in \
  roles/datastore.user \
  roles/cloudtasks.enqueuer \
  roles/secretmanager.secretAccessor \
  roles/run.invoker; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${role}" \
    --condition=None >/dev/null
done

# The SA enqueues Cloud Tasks that carry an OIDC token minted for *itself*
# (oidc_token.service_account_email = digest-bot-sa). Creating such a task
# requires iam.serviceAccounts.actAs on that SA, i.e. the SA needs
# roles/iam.serviceAccountUser on itself — otherwise create_task returns
# PERMISSION_DENIED and every deferred slow command fails to enqueue.
echo "==> Allow ${SA_NAME} to actAs itself (OIDC task tokens)"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/iam.serviceAccountUser" \
  --project "${PROJECT_ID}" >/dev/null

echo "==> Artifact Registry repo ${REPO}"
if ! gcloud artifacts repositories describe "${REPO}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker --location="${REGION}"
fi

echo "==> Firestore (NATIVE mode — irreversible)"
if ! gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  gcloud firestore databases create --location="${REGION}" --type=firestore-native
else
  echo "    exists (leaving as-is)"
fi

echo "==> Firestore TTL policy on processed_commits.expire_at"
gcloud firestore fields ttls update expire_at \
  --collection-group=processed_commits --enable-ttl >/dev/null 2>&1 || \
  echo "    TTL update skipped (may already be enabled)"

echo "==> Firestore TTL policy on processed_posts.expire_at"
gcloud firestore fields ttls update expire_at \
  --collection-group=processed_posts --enable-ttl >/dev/null 2>&1 || \
  echo "    TTL update skipped (may already be enabled)"

echo "==> Cloud Tasks queues"
for q in "${FANOUT_QUEUE}" "${FOLLOWUPS_QUEUE}"; do
  if ! gcloud tasks queues describe "${q}" --location="${REGION}" >/dev/null 2>&1; then
    gcloud tasks queues create "${q}" --location="${REGION}"
  fi
done
# Enforce the low retry cap on the fan-out queue (idempotent; also converges a
# queue that already exists with the Cloud Tasks default of 100 attempts).
gcloud tasks queues update "${FANOUT_QUEUE}" --location="${REGION}" \
  --max-attempts="${FANOUT_MAX_ATTEMPTS}"

echo "==> Secret Manager secrets (create empty; set values with:"
echo "    printf %s \"<value>\" | gcloud secrets versions add <NAME> --data-file=-)"
# GITHUB_TOKEN_PRIVATE is OPTIONAL — a least-privilege PAT for reading tracked
# *private* repos. It is created here (and covered by the SA's project-level
# secretAccessor) but is NOT mounted by cloudbuild.yaml, so deploys stay green
# even when it has no version. Attach it once set (see DEPLOY.md → Private repos).
for secret in DISCORD_PUBLIC_KEY DISCORD_TOKEN DISCORD_APP_ID GITHUB_TOKEN ANTHROPIC_API_KEY GITHUB_TOKEN_PRIVATE; do
  if ! gcloud secrets describe "${secret}" >/dev/null 2>&1; then
    gcloud secrets create "${secret}" --replication-policy=automatic
  fi
done

# Optional: GitHub App (per-member private-repo digest; SPEC-GHAPP / ADR-0002).
# Only the sensitive values are secrets — the App private key (signs the App JWT
# that mints installation tokens) and the webhook secret (verifies inbound
# X-Hub-Signature-256). The App id and slug are non-sensitive runtime env, set
# via --update-env-vars, not secrets. Opt in with ENABLE_GITHUB_APP=1 so default
# deploys don't provision secrets for an unused feature.
if [[ "${ENABLE_GITHUB_APP:-0}" == "1" ]]; then
  echo "==> GitHub App secrets (optional member private-repo path)"
  for secret in GITHUB_APP_PRIVATE_KEY GITHUB_APP_WEBHOOK_SECRET; do
    if ! gcloud secrets describe "${secret}" >/dev/null 2>&1; then
      gcloud secrets create "${secret}" --replication-policy=automatic
    fi
  done
fi

if [[ -z "${SERVICE_URL}" ]]; then
  cat <<EOF

==> SERVICE_URL not set — stopping before Scheduler/alert setup.
    Deploy the service first (gcloud builds submit --config deploy/cloudbuild.yaml),
    grab its URL, then re-run:
      PROJECT_ID=${PROJECT_ID} REGION=${REGION} SERVICE_URL=<url> bash deploy/setup.sh
EOF
  exit 0
fi

echo "==> Cloud Scheduler daily-digest (OIDC -> ${SERVICE_URL}/tasks/digest/run)"
if ! gcloud scheduler jobs describe daily-digest --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs create http daily-digest \
    --location="${REGION}" \
    --schedule="0 ${DIGEST_HOUR_UTC} * * *" \
    --time-zone="Etc/UTC" \
    --uri="${SERVICE_URL}/tasks/digest/run" \
    --http-method=POST \
    --oidc-service-account-email="${SA_EMAIL}" \
    --oidc-token-audience="${SERVICE_URL}"
else
  gcloud scheduler jobs update http daily-digest \
    --location="${REGION}" \
    --schedule="0 ${DIGEST_HOUR_UTC} * * *" \
    --uri="${SERVICE_URL}/tasks/digest/run" \
    --oidc-service-account-email="${SA_EMAIL}" \
    --oidc-token-audience="${SERVICE_URL}"
fi

echo "==> Digest SLO: log-based metric + alert on missing heartbeat"
if ! gcloud logging metrics describe digest_heartbeat >/dev/null 2>&1; then
  gcloud logging metrics create digest_heartbeat \
    --description="Successful daily digest posts" \
    --log-filter='jsonPayload.message="digest_heartbeat"'
fi
echo "    Create an alert policy that pages when logging/user/digest_heartbeat"
echo "    has zero data points over a 26h window (Console → Monitoring → Alerting,"
echo "    or 'gcloud alpha monitoring policies create' with an absence condition)."

cat <<EOF

==> Done. Remaining manual steps:
  1. Set secret values (see the Secret Manager step above).
  2. Front /admin/* with IAP (Console → Security → Identity-Aware Proxy),
     grant your account roles/iap.httpsResourceAccessor on the service.
  3. Register slash commands:  uv run python -m scripts.register_commands --guild <GUILD_ID>
  4. In the Discord Developer Portal, set the Interactions Endpoint URL to
     ${SERVICE_URL}/interactions  (Discord will PING → expects PONG).
EOF
