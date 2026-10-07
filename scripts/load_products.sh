#!/usr/bin/env bash
# Load generator/products.csv (the same catalog the generator publishes) into
# silver.products via the bucket's seed/ prefix. Replaces the table on every run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
tf() { terraform -chdir="$ROOT/infra" output -raw "$1"; }
PROJECT=$(tf project_id)
URI="gs://$(tf bucket_name)/seed/products.csv"

gcloud storage cp "$ROOT/generator/products.csv" "$URI" --project="$PROJECT"
bq load --project_id="$PROJECT" --location="$(tf region)" --replace --source_format=CSV \
  --skip_leading_rows=1 "silver.products" "$URI" \
  product_id:STRING,name:STRING,category:STRING,brand:STRING
