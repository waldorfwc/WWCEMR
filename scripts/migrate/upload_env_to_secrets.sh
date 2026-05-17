#!/usr/bin/env bash
# Bulk-upload a local .env file into Google Secret Manager and grant access
# to the backend@/worker@ service accounts.
#
# - Idempotent: existing secrets get a new version; new ones are created.
# - Skips empty values and comment lines.
# - Skips DATABASE_URL (managed separately during DB setup).
# - Never echoes secret values to stdout.
#
# Usage:
#   bash scripts/migrate/upload_env_to_secrets.sh [path/to/.env]
#
# Defaults: backend/.env, project wwc-solutions.

set -euo pipefail

ENV_FILE="${1:-backend/.env}"
PROJECT="${PROJECT:-wwc-solutions}"
SA_BACKEND="backend@${PROJECT}.iam.gserviceaccount.com"
SA_WORKER="worker@${PROJECT}.iam.gserviceaccount.com"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERR: env file not found: $ENV_FILE" >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERR: gcloud CLI not on PATH" >&2
  exit 1
fi

# Names that match Cloud-Run env-var convention: lowercase, dashes only.
# We map FOO_BAR -> foo-bar
to_secret_name() {
  printf '%s' "$1" | tr '[:upper:]_' '[:lower:]-'
}

create_or_update_secret() {
  local key="$1"
  local val="$2"
  local secret_name
  secret_name="$(to_secret_name "$key")"

  # Skip a few we manage explicitly elsewhere
  case "$key" in
    DATABASE_URL) echo "  skip $key (managed separately)"; return 0 ;;
  esac

  # Empty value? Skip (no point creating an empty secret).
  if [[ -z "$val" ]]; then
    echo "  skip $key (empty)"
    return 0
  fi

  if gcloud secrets describe "$secret_name" --project="$PROJECT" >/dev/null 2>&1; then
    # Exists — add a new version
    printf '%s' "$val" | gcloud secrets versions add "$secret_name" \
      --data-file=- --project="$PROJECT" >/dev/null
    echo "  updated $secret_name"
  else
    # Create
    printf '%s' "$val" | gcloud secrets create "$secret_name" \
      --data-file=- --replication-policy=automatic --project="$PROJECT" >/dev/null
    echo "  created $secret_name"
  fi

  # Grant access (idempotent — gcloud short-circuits if binding exists)
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:$SA_BACKEND" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT" >/dev/null 2>&1 || true
  gcloud secrets add-iam-policy-binding "$secret_name" \
    --member="serviceAccount:$SA_WORKER" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT" >/dev/null 2>&1 || true
}

echo "Uploading $ENV_FILE -> Secret Manager (project=$PROJECT)..."

# Track names for the deploy command at the end
declare -a UPLOADED_KEYS=()

# Read .env line by line. Handles KEY=VALUE, KEY="QUOTED VAL", KEY='SINGLE',
# and skips blank lines / comments.
while IFS= read -r line || [[ -n "$line" ]]; do
  # strip CR (in case of CRLF), leading whitespace
  line="${line%$'\r'}"
  line="${line#"${line%%[![:space:]]*}"}"
  # skip empties + comments
  [[ -z "$line" ]] && continue
  [[ "$line" == \#* ]] && continue
  # must contain =
  [[ "$line" != *=* ]] && continue

  key="${line%%=*}"
  val="${line#*=}"

  # Strip surrounding quotes from value, if any
  if [[ "$val" == \"*\" ]] || [[ "$val" == \'*\' ]]; then
    val="${val:1:${#val}-2}"
  fi

  # Validate key is shell-identifier-shaped
  if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "  skip malformed line: $key"
    continue
  fi

  create_or_update_secret "$key" "$val"
  UPLOADED_KEYS+=("$key")
done < "$ENV_FILE"

echo
echo "Uploaded ${#UPLOADED_KEYS[@]} secret(s)."
echo
echo "=== Cloud Run --set-secrets flag (copy/paste) ==="
flags=""
for k in "${UPLOADED_KEYS[@]}"; do
  s="$(to_secret_name "$k")"
  flags+="${k}=${s}:latest,"
done
# trim trailing comma
flags="${flags%,}"
echo "--set-secrets=\"${flags}\""
echo
echo "Next: redeploy backend with that flag, e.g."
echo "  gcloud run services update backend --region=us-east4 --project=${PROJECT} \\"
echo "    --set-secrets=\"${flags}\""
