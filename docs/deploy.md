# Deploy cheatsheet

Production runs in GCP project `wwc-solutions`, region `us-east4`.
Domain `gw.waldorfwomenscare.com` → Cloudflare → cloudflared (GCE VM
`tunnel-host`) → Cloud Run `frontend` → Cloud Run `backend` → Cloud SQL
`app-db` + GCS `gs://wwc-app-docs`.

All commands assume `gcloud` is on PATH and authenticated as
`ocooke@waldorfwomenscare.com`.

---

## Backend code change

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project

# 1. Build a new image (bump the tag; v5, v6, ...)
gcloud builds submit backend/ \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/backend:vN \
  --project=wwc-solutions --region=us-east4

# 2. Roll the Cloud Run service onto the new revision
gcloud run services update backend \
  --region=us-east4 --project=wwc-solutions \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:vN
```

A new revision starts, Cloud Run shifts traffic to it after a
successful health check. Previous revision stays around for quick
rollback. **Background jobs use the same image** — re-point them with
the "All jobs at once" snippet below.

## Frontend code change

```bash
cd /Users/wwcclaudecode/Documents/wwc-era-project

gcloud builds submit frontend/ \
  --tag=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:vN \
  --project=wwc-solutions --region=us-east4

gcloud run services update frontend \
  --region=us-east4 --project=wwc-solutions \
  --image=us-east4-docker.pkg.dev/wwc-solutions/app/frontend:vN
```

If you change the backend URL the frontend should proxy to, also pass
`--update-env-vars=BACKEND_URL=https://...`.

## Background job code change (all 11 at once)

The Cloud Run Jobs reuse the **backend** image. After updating backend,
roll all jobs onto the new tag:

```bash
TAG=vN
for job in $(gcloud run jobs list --region=us-east4 --project=wwc-solutions --format="value(metadata.name)"); do
  gcloud run jobs update "$job" --region=us-east4 --project=wwc-solutions \
    --image=us-east4-docker.pkg.dev/wwc-solutions/app/backend:$TAG --quiet
done
```

Or re-run the provisioner — it's idempotent and re-applies the latest tag:

```bash
bash scripts/migrate/create_cloud_run_jobs.sh
```

## Database schema change

The app's `init_db()` runs `Base.metadata.create_all(...)` on startup —
**new tables and indexes appear automatically** when a new backend
revision rolls out.

**Adding a column to an existing table:** add an entry to the `needed`
list in `backend/app/database.py:_apply_lightweight_migrations()` —
`(table, column, "TYPE")`. Next backend deploy picks it up. The helper
auto-translates `DATETIME`/`BOOLEAN DEFAULT 0|1` for Postgres.

**Bigger migrations** (rename, drop, data fix): write a one-shot
function alongside `_migrate_billing_doc_status_open_to_new` and hook
it into `init_db()`. Idempotent — runs every boot, does nothing once
applied.

## Adding a secret

```bash
# Create the secret (interactive — paste then ^D)
gcloud secrets create my-secret --replication-policy=automatic --project=wwc-solutions
echo -n "actual-value" | gcloud secrets versions add my-secret --data-file=- --project=wwc-solutions

# Grant the running services access
for sa in backend worker; do
  gcloud secrets add-iam-policy-binding my-secret \
    --member="serviceAccount:${sa}@wwc-solutions.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" --project=wwc-solutions
done

# Mount it on the backend (env var MY_SECRET <- my-secret:latest)
gcloud run services update backend --region=us-east4 --project=wwc-solutions \
  --update-secrets="MY_SECRET=my-secret:latest"

# Same for jobs that need it (example: just fax-poller)
gcloud run jobs update fax-poller --region=us-east4 --project=wwc-solutions \
  --update-secrets="MY_SECRET=my-secret:latest"
```

For a bulk upload of `.env` → Secret Manager, use
`scripts/migrate/upload_env_to_secrets.sh`.

## Cloud Scheduler triggers

```bash
# List (with cron + state)
gcloud scheduler jobs list --location=us-east4 --project=wwc-solutions \
  --format="table(name.basename(),schedule,state)"

# Pause / resume one
gcloud scheduler jobs pause  fax-poller-trigger --location=us-east4 --project=wwc-solutions
gcloud scheduler jobs resume fax-poller-trigger --location=us-east4 --project=wwc-solutions

# Fire one manually right now (useful for testing)
gcloud scheduler jobs run fax-poller-trigger --location=us-east4 --project=wwc-solutions

# Run a Cloud Run Job ad-hoc, wait for completion
gcloud run jobs execute fax-poller --region=us-east4 --project=wwc-solutions --wait
```

To add a new scheduled task, append a row to the `JOBS` array in
`scripts/migrate/create_cloud_run_jobs.sh` and re-run it.

## Read-only / debug

```bash
# Last 50 lines from backend
gcloud run services logs read backend --region=us-east4 --project=wwc-solutions --limit=50

# Filter for an endpoint or error
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="backend" AND textPayload:"/api/claims"' \
  --limit=20 --project=wwc-solutions --format="value(timestamp,textPayload)"

# Cloud Run revision list (which one's live, traffic split)
gcloud run revisions list --service=backend --region=us-east4 --project=wwc-solutions

# Cloud SQL ops + state
gcloud sql operations list --instance=app-db --project=wwc-solutions --limit=5
gcloud sql instances describe app-db --project=wwc-solutions --format="value(state,settings.tier)"

# Cloud Run Job execution history
gcloud run jobs executions list --job=fax-poller --region=us-east4 --project=wwc-solutions --limit=10

# GCS bucket usage
gcloud storage du gs://wwc-app-docs --readable-sizes --summarize --project=wwc-solutions
```

## Rollback

A bad deploy can be reverted in seconds by routing traffic back to the
previous revision:

```bash
# See revisions; pick the previous "good" one (the one before this)
gcloud run revisions list --service=backend --region=us-east4 --project=wwc-solutions

# Route 100% of traffic back to it
gcloud run services update-traffic backend --region=us-east4 --project=wwc-solutions \
  --to-revisions=backend-00003-6ht=100
```

Same pattern for `frontend`.

To roll back a Cloud Run Job, `update` it back to the prior image tag.

## Connecting to Cloud SQL from your Mac

Cloud SQL is private-IP-only. Two paths:

**Auth Proxy** (recommended for ad-hoc psql / migrations):
```bash
cloud-sql-proxy --private-ip wwc-solutions:us-east4:app-db --port=5433 &
PWD=$(gcloud secrets versions access latest --secret=cloudsql-postgres-root-password --project=wwc-solutions)
psql "host=127.0.0.1 port=5433 user=postgres dbname=wwc_app sslmode=disable"
```
Note: with `--private-ip` the proxy needs to reach 10.x.x.x — only
works from inside the GCP VPC. From your Mac (outside VPC), you have
to either skip `--private-ip` (and temporarily enable Cloud SQL public
IP + add your WAN to authorized networks), OR SSH into `tunnel-host`
and run psql from there.

**Quick public-IP path for big migrations:**
```bash
WAN=$(curl -s https://api.ipify.org)
gcloud sql instances patch app-db --assign-ip --authorized-networks="$WAN/32" --quiet --project=wwc-solutions
# ... run your migration script against the public IP ...
gcloud sql instances patch app-db --no-assign-ip --clear-authorized-networks --quiet --project=wwc-solutions
```
Always close it back up afterward.

## Gotchas

- **Cloud Run scales to zero** after ~15 min idle → first request after
  idle has 10–30 s cold-start. Set `--min-instances=1` on backend
  if/when this becomes user-visible (~$5–10/mo).
- **Backend uvicorn reload** is local dev only. Cloud Run runs
  `--workers 1` with no reload — code changes require a new image.
- **Don't commit secrets** to git, ever. Add them via the secret flow
  above. The `.env` file is gitignored.
- **`init_db()` is idempotent** but does a small amount of work each
  boot (alters + seeds). Don't add long-running migrations to it —
  put those in a script under `scripts/migrate/`.
- **Image tag bumps are essential.** Cloud Run won't redeploy if the
  image tag is unchanged — it'll think it's already at the desired
  state. Always increment.
- **Domain mapping not in us-east4.** If you ever want to drop the
  Cloudflare tunnel entirely, you'll need a Cloud Load Balancer
  (~$20/mo) or move services to a region with mapping support.
