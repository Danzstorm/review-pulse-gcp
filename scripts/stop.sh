#!/usr/bin/env bash
# Stop every active Dataflow job and wait until none is left running (and billing).
# Default is drain: stop reading, finish in-flight data. --cancel stops immediately.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=$(terraform -chdir="$ROOT/infra" output -raw project_id)
REGION=$(terraform -chdir="$ROOT/infra" output -raw region)
ACTION=drain
[[ "${1:-}" == "--cancel" ]] && ACTION=cancel

# Not --status=active: it misses Queued jobs, e.g. a Flex Template whose launcher VM is still up.
active() {
  gcloud dataflow jobs list --project="$PROJECT" --region="$REGION" --status=all \
    --filter="NOT state:(Done Failed Cancelled Drained Updated)" --format="value(id)"
}

JOBS=$(active)
if [[ -z "$JOBS" ]]; then
  echo "No active Dataflow jobs."
  exit 0
fi
for job in $JOBS; do
  gcloud dataflow jobs "$ACTION" "$job" --project="$PROJECT" --region="$REGION"
done
until [[ -z "$(active)" ]]; do
  echo "Waiting for jobs to stop..."
  sleep 20
done
echo "All Dataflow jobs stopped."
