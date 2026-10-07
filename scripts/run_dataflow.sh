#!/usr/bin/env bash
# Launch the streaming pipeline from the Flex Template (build it first with
# scripts/build_template.sh). Only gcloud is needed: no local Python, Beam or Java.
# Every value comes from `terraform output`. Stop the job with scripts/stop.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf() { terraform -chdir="$ROOT/infra" output -raw "$1"; }

PROJECT=$(tf project_id)
REGION=$(tf region)
BUCKET=$(tf bucket_name)

gcloud dataflow flex-template run "review-pulse-$(date -u +%Y%m%d-%H%M%S)" \
  --project="$PROJECT" \
  --region="$REGION" \
  --template-file-gcs-location="gs://$BUCKET/templates/review-pulse.json" \
  --service-account-email="$(tf dataflow_runner_email)" \
  --staging-location="gs://$BUCKET/staging" \
  --temp-location="gs://$BUCKET/tmp" \
  --enable-streaming-engine \
  --worker-machine-type=e2-standard-2 \
  --max-workers=1 \
  --parameters="input_subscription=projects/$PROJECT/subscriptions/$(tf pubsub_subscription),bronze_table=$(tf bronze_table),bucket=$BUCKET"
