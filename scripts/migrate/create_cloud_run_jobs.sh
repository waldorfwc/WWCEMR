#!/usr/bin/env bash
# Create one Cloud Run Job + one Cloud Scheduler trigger per scheduled
# background task. Mirrors the APScheduler config in
# backend/app/services/fax_poller.py:start_scheduler() so the cutover
# from in-process scheduling to GCP scheduling is 1:1.
#
# Idempotent — uses `update-or-create` (deploys override an existing
# definition cleanly via `gcloud ... deploy` / `gcloud ... update`).
#
# Usage:
#   bash scripts/migrate/create_cloud_run_jobs.sh

set -euo pipefail

export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"

PROJECT="wwc-solutions"
REGION="us-east4"
# Mirror whatever image the backend SERVICE currently runs, so re-running
# this never downgrades the jobs to a stale hardcoded tag.
IMAGE=$(gcloud run services describe backend --region="$REGION" \
  --project="$PROJECT" --format="value(spec.template.spec.containers[0].image)")
WORKER_SA="worker@${PROJECT}.iam.gserviceaccount.com"
TZ="America/New_York"

# Build the secrets flag dynamically — mirrors what backend has, minus the
# consent-* sender secrets which only the backend SERVICE needs (no job runs
# the consent/enrollment code that reads them), and minus the stripe-* secrets
# which only the backend SERVICE (Stripe webhook) reads — the worker SA isn't
# granted accessor on them, so mounting them would fail job deploys.
SECRETS_FLAG=$(gcloud secrets list --project="$PROJECT" --format="value(name)" \
  | grep -v -E "^(cloudsql-postgres-root-password|database-url|consent-provider-email|consent-provider-name|consent-witness-email|consent-witness-name|stripe-secret-key|stripe-webhook-secret)$" \
  | awk '{env=toupper($1); gsub("-","_",env); printf "%s=%s:latest,",env,$1}' \
  | sed 's/,$//')
ALL_SECRETS="DATABASE_URL=database-url:latest,${SECRETS_FLAG}"

# job_name  cron-spec                       cli-arg
JOBS=(
  "fax-poller             */2 * * * *      fax_poller"
  "checklist-generate     5 0 * * *        checklist_generate"
  "checklist-morning      30 7 * * *       checklist_morning"
  "checklist-eod          0 17 * * *       checklist_eod"
  "checklist-escalations  15 8-18 * * 1-5  checklist_escalations"
  "google-workspace-sync  30 * * * *       google_workspace_sync"
  "surgery-escalations    45 8-18 * * 1-5  surgery_escalations"
  "surgery-release-sweep  0 9 * * 1-5      surgery_release_sweep"
  "larc-sweeps            15 9 * * 1-5     larc_sweeps"
  "pellet-stale-sweep     30 0 * * *       pellet_stale_sweep"
  "missing-charges-weekly 0 8 * * 1        missing_charges_weekly"
  "missing-charges-triage 0 8 * * 4        missing_charges_triage_reminder"
)

create_or_update_job() {
  local job_name="$1"
  local cli_arg="$2"

  echo "  → Cloud Run Job: $job_name"

  # Cloud Run Jobs create-or-update flow: try create, fall back to update.
  if gcloud run jobs describe "$job_name" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud run jobs update "$job_name" \
      --image="$IMAGE" \
      --region="$REGION" \
      --project="$PROJECT" \
      --service-account="$WORKER_SA" \
      --network=default --subnet=default --vpc-egress=private-ranges-only \
      --memory=1Gi --cpu=1 \
      --max-retries=1 --task-timeout=15m \
      --set-secrets="$ALL_SECRETS" \
      --update-env-vars=STORAGE_BACKEND=gcs,DOCUMENTS_GCS_BUCKET=wwc-app-docs \
      --command=python \
      --args="-m,app.jobs.run,${cli_arg}" \
      --quiet >/dev/null
  else
    gcloud run jobs create "$job_name" \
      --image="$IMAGE" \
      --region="$REGION" \
      --project="$PROJECT" \
      --service-account="$WORKER_SA" \
      --network=default --subnet=default --vpc-egress=private-ranges-only \
      --memory=1Gi --cpu=1 \
      --max-retries=1 --task-timeout=15m \
      --set-secrets="$ALL_SECRETS" \
      --set-env-vars=STORAGE_BACKEND=gcs,DOCUMENTS_GCS_BUCKET=wwc-app-docs \
      --command=python \
      --args="-m,app.jobs.run,${cli_arg}" \
      --quiet >/dev/null
  fi
}

create_or_update_scheduler() {
  local job_name="$1"
  local cron="$2"
  local trigger="${job_name}-trigger"
  local uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${job_name}:run"

  if gcloud scheduler jobs describe "$trigger" --location="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$trigger" \
      --location="$REGION" --project="$PROJECT" \
      --schedule="$cron" --time-zone="$TZ" \
      --uri="$uri" --http-method=POST \
      --oauth-service-account-email="$WORKER_SA" \
      --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
      --quiet >/dev/null
  else
    gcloud scheduler jobs create http "$trigger" \
      --location="$REGION" --project="$PROJECT" \
      --schedule="$cron" --time-zone="$TZ" \
      --uri="$uri" --http-method=POST \
      --oauth-service-account-email="$WORKER_SA" \
      --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
      --quiet >/dev/null
  fi
}

for row in "${JOBS[@]}"; do
  # Each JOBS entry is "name  cron  cli_arg" — the cron can have spaces.
  # Parse: first token = name, last = cli_arg, middle = cron.
  job_name=$(echo "$row" | awk '{print $1}')
  cli_arg=$(echo "$row"  | awk '{print $NF}')
  cron=$(echo "$row"     | awk '{for (i=2; i<NF; i++) printf "%s%s", $i, (i<NF-1?" ":"")}')

  create_or_update_job "$job_name" "$cli_arg"
  create_or_update_scheduler "$job_name" "$cron"
  echo "    cron: $cron  → arg: $cli_arg"
done

echo
echo "Done."
