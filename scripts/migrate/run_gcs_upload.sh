#!/usr/bin/env bash
# Resilient gsutil rsync wrapper. Restarts on failure with backoff.
# Use caffeinate -i to keep the Mac awake during the long upload.
#
# Usage:  bash run_gcs_upload.sh <source-path> <gs://dest-uri>
#
# Designed to run under nohup so the Mac can be locked / lid closed
# without killing the upload.

set -uo pipefail

SRC="${1:?source path required}"
DEST="${2:?gs:// destination required}"
PROJECT="${PROJECT:-wwc-solutions}"
PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
export PATH

ATTEMPT=0
while true; do
  ATTEMPT=$((ATTEMPT + 1))
  echo "=== attempt #$ATTEMPT at $(date '+%Y-%m-%d %H:%M:%S') ==="
  caffeinate -i gcloud storage rsync \
    --recursive \
    "$SRC" "$DEST" \
    --project="$PROJECT"
  rc=$?
  echo "=== exit code $rc at $(date '+%Y-%m-%d %H:%M:%S') ==="
  if [ "$rc" -eq 0 ]; then
    echo "Clean exit. Verifying by re-running once more (should be no-op)."
    caffeinate -i gcloud storage rsync \
      --recursive \
      "$SRC" "$DEST" \
      --project="$PROJECT"
    rc2=$?
    echo "Verification exit code: $rc2"
    if [ "$rc2" -eq 0 ]; then
      echo "=== upload appears complete at $(date '+%Y-%m-%d %H:%M:%S') ==="
      exit 0
    fi
  fi
  echo "Sleeping 30s before retry..."
  sleep 30
done
