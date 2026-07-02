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
DIGEST_HOUR_UTC="${DIGEST_HOUR_UTC:-13}"
# SERVICE_URL is the deployed Cloud Run URL; required for Scheduler/Tasks targets
# and the OIDC audience. Deploy once (Cloud Build) to learn it, then re-run.
SERVICE_URL="${SERVICE_URL:-}"

gcloud config set project "${PROJECT_ID}" >/dev/null

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

echo "==> Cloud Tasks queues"
for q in "${FANOUT_QUEUE}" "${FOLLOWUPS_QUEUE}"; do
  if ! gcloud tasks queues describe "${q}" --location="${REGION}" >/dev/null 2>&1; then
    gcloud tasks queues create "${q}" --location="${REGION}"
  fi
done

echo "==> Secret Manager secrets (create empty; set values with:"
echo "    printf %s \"<value>\" | gcloud secrets versions add <NAME> --data-file=-)"
for secret in DISCORD_PUBLIC_KEY DISCORD_TOKEN DISCORD_APP_ID GITHUB_TOKEN ANTHROPIC_API_KEY; do
  if ! gcloud secrets describe "${secret}" >/dev/null 2>&1; then
    gcloud secrets create "${secret}" --replication-policy=automatic
  fi
done

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
