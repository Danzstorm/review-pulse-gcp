#!/usr/bin/env bash
# Build the agent image with Cloud Build and roll it out to Cloud Run.
# First time: terraform apply (identity), run this script, then set deploy_agent = true
# in terraform.tfvars and terraform apply again (creates the service).
#
# Every value is a Terraform output name. If an upper-case environment variable of the same
# name is set (PROJECT_ID, REGION, BUILDER_EMAIL, BUILDS_BUCKET, AGENT_IMAGE) it wins, so
# CI can run this without Terraform state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
val() {
  local env_name="${1^^}"
  if [[ -n "${!env_name:-}" ]]; then echo "${!env_name}"; else terraform -chdir="$ROOT/infra" output -raw "$1"; fi
}

PROJECT=$(val project_id)
REGION=$(val region)
IMAGE=$(val agent_image)
BUILDER=$(val builder_email)
BUCKET=$(val builds_bucket)
TAG=$(git -C "$ROOT" rev-parse --short HEAD)
[[ -z "$(git -C "$ROOT" status --porcelain -- agent)" ]] || TAG="$TAG-dirty"

gcloud builds submit "$ROOT/agent" \
  --project="$PROJECT" --region="$REGION" \
  --config="$ROOT/agent/cloudbuild.yaml" \
  --substitutions="_IMAGE=$IMAGE:$TAG,_LATEST=$IMAGE:latest" \
  --service-account="projects/$PROJECT/serviceAccounts/$BUILDER" \
  --gcs-source-staging-dir="gs://$BUCKET/source"

if gcloud run services describe review-pulse-agent --project="$PROJECT" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update review-pulse-agent --project="$PROJECT" --region="$REGION" --image="$IMAGE:$TAG"
  echo "Deployed $IMAGE:$TAG"
else
  echo "Image pushed ($IMAGE:$TAG). Set deploy_agent = true and run terraform apply to create the service."
fi
