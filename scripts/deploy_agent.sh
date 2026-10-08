#!/usr/bin/env bash
# Build the agent image with Cloud Build and roll it out to Cloud Run.
# First time: terraform apply (identity), run this script, then set deploy_agent = true
# in terraform.tfvars and terraform apply again (creates the service).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf() { terraform -chdir="$ROOT/infra" output -raw "$1"; }

PROJECT=$(tf project_id)
REGION=$(tf region)
TAG=$(git -C "$ROOT" rev-parse --short HEAD)
[[ -z "$(git -C "$ROOT" status --porcelain -- agent)" ]] || TAG="$TAG-dirty"
IMAGE="$(tf agent_image)"

gcloud builds submit "$ROOT/agent" \
  --project="$PROJECT" --region="$REGION" \
  --config="$ROOT/agent/cloudbuild.yaml" \
  --substitutions="_IMAGE=$IMAGE:$TAG,_LATEST=$IMAGE:latest" \
  --service-account="projects/$PROJECT/serviceAccounts/$(tf builder_email)" \
  --gcs-source-staging-dir="gs://$(tf builds_bucket)/source"

if gcloud run services describe review-pulse-agent --project="$PROJECT" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update review-pulse-agent --project="$PROJECT" --region="$REGION" --image="$IMAGE:$TAG"
  echo "Deployed $IMAGE:$TAG"
else
  echo "Image pushed ($IMAGE:$TAG). Set deploy_agent = true and run terraform apply to create the service."
fi
