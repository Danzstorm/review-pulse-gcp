#!/usr/bin/env bash
# Launch the streaming pipeline on Dataflow. Every value comes from `terraform output`,
# so the infra is the single source of configuration. Stop it with scripts/stop.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf() { terraform -chdir="$ROOT/infra" output -raw "$1"; }

PROJECT=$(tf project_id)
REGION=$(tf region)
BUCKET=$(tf bucket_name)
# ADC may carry another project as quota project; scope the override to this process only.
export GOOGLE_CLOUD_QUOTA_PROJECT="$PROJECT"
PYTHON="$ROOT/.venv/Scripts/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"

"$PYTHON" "$ROOT/pipelines/dataflow/pipeline.py" \
  --runner=DataflowRunner \
  --project="$PROJECT" \
  --region="$REGION" \
  --job_name="review-pulse-$(date -u +%Y%m%d-%H%M%S)" \
  --service_account_email="$(tf dataflow_runner_email)" \
  --temp_location="gs://$BUCKET/tmp" \
  --staging_location="gs://$BUCKET/staging" \
  --enable_streaming_engine \
  --worker_machine_type=e2-standard-2 \
  --max_num_workers=1 \
  --input_subscription="projects/$PROJECT/subscriptions/$(tf pubsub_subscription)" \
  --bronze_table="$(tf bronze_table)" \
  --bucket="$BUCKET"
