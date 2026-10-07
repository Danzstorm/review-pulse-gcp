#!/usr/bin/env bash
# Build the launcher image with Cloud Build and publish the Flex Template spec to GCS.
# Re-run after any change under pipelines/dataflow/. The image tag is the git commit,
# suffixed -dirty when pipelines/dataflow/ has uncommitted changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf() { terraform -chdir="$ROOT/infra" output -raw "$1"; }

PROJECT=$(tf project_id)
REGION=$(tf region)
BUCKET=$(tf bucket_name)
TAG=$(git -C "$ROOT" rev-parse --short HEAD)
[[ -z "$(git -C "$ROOT" status --porcelain -- pipelines/dataflow)" ]] || TAG="$TAG-dirty"
IMAGE="$(tf template_image):$TAG"
SPEC="gs://$BUCKET/templates/review-pulse.json"

gcloud builds submit "$ROOT/pipelines/dataflow" \
  --project="$PROJECT" \
  --region="$REGION" \
  --config="$ROOT/pipelines/dataflow/cloudbuild.yaml" \
  --substitutions="_IMAGE=$IMAGE" \
  --service-account="projects/$PROJECT/serviceAccounts/$(tf builder_email)" \
  --gcs-source-staging-dir="gs://$(tf builds_bucket)/source"

gcloud dataflow flex-template build "$SPEC" \
  --project="$PROJECT" \
  --image="$IMAGE" \
  --sdk-language=PYTHON \
  --metadata-file="$ROOT/pipelines/dataflow/metadata.json"

echo "Template $SPEC -> $IMAGE"
